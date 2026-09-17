"""Single-owner enforcement for the process-global MT5 session.

Why this module exists
----------------------

MT5 state is process-global: one live terminal connection, one authenticated
account at a time, switched in place (see ``app.core.mt5_session``). The session
manager serializes access with a ``threading.RLock`` — which protects threads
*inside one process only*. It provides nothing across OS processes: two
application processes pointed at the same terminal would each authenticate that
terminal independently, and because a terminal has one current account, one
process could read the account the other just switched to.

Nothing in a plain ``uvicorn app.main:app`` prevents that: ``--workers N``, a
second launch of the same app, or two deployments on one host all produce
processes that share a terminal. So the invariant is enforced here, at the
application boundary, instead of being documented and hoped for.

Mechanism
---------

An exclusive **OS-level lock on a lock file**, held for the process lifetime:

* the kernel, not this code, decides who owns it — there is no check-then-act
  window for two processes to race through;
* it is released automatically when the process exits, including on a crash, so
  there is no stale lock for an operator to clear by hand;
* it needs no port, no daemon, no network, no distributed lock manager and no
  configuration, which is why it fits a modular monolith.

One lock per TERMINAL, not per application: the lock name is derived from
``MT5_TERMINAL_PATH``. Two processes driving *different* terminals are legitimate
(with an explicit path each) and do not block each other; two processes that
would drive the SAME terminal — including both leaving the path unset, i.e. both
letting the package find the terminal — cannot both start.

Failure behavior
----------------

``acquire()`` fails closed with ``MT5OwnershipError`` (a ``RuntimeError``) when
the lock is held by another process or cannot be established at all. It is
raised from the FastAPI lifespan, so the process refuses to start rather than
serve MT5 reads it cannot isolate. This module never touches MT5, never imports
it and holds no credential: ownership is decided before any terminal is
contacted, and the error text names the setting to fix, never a secret.

Scope note: the lock lives in the operating system's temporary directory, which
is per user. That is the correct scope, because an MT5 terminal is per Windows
user session: two users on one host have their own terminals to own. This is a
guard for the current single-process topology, not the future worker-pool
architecture (roadmap P8/P12), where several processes own several terminals
deliberately — that decision is what this module's per-terminal lock name is
shaped to allow, without implementing any of it.
"""
import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import IO

# Where the owner lock files live. Module-level so tests can point it at a
# temporary directory; a deployment never changes it (the OS temp directory is
# per user, which is exactly the terminal's own scope).
DEFAULT_LOCK_DIRECTORY = Path(tempfile.gettempdir())

# The lock name for a deployment that did not pin a terminal executable: such a
# process lets the package find "the" terminal, so all of them share one owner.
_UNPINNED_TERMINAL_KEY = "unpinned-terminal"


class MT5OwnershipError(RuntimeError):
    """This process may not own the MT5 terminal (another owner, or no lock).

    A ``RuntimeError`` so the established failure mapping keeps working: it is
    raised at startup, where the app simply refuses to run. The message is
    deliberately explicit — unlike a credential failure, an operator must be able
    to see WHICH terminal already has an owner and what to change — and it names
    no secret: a lock path and a pid are not credentials.
    """


def ownership_lock_path(terminal_path: str | None, *, directory: Path | None = None) -> Path:
    """The lock file for one terminal, so ownership is per terminal.

    A pinned ``MT5_TERMINAL_PATH`` identifies its terminal as that path; an
    unset/blank path means "whatever terminal the package finds", which is one
    shared key. The path is hashed to a short digest so any terminal location is
    a safe file name.
    """
    key = (terminal_path or "").strip()
    if not key:
        digest = _UNPINNED_TERMINAL_KEY
    else:
        normalized = os.path.normcase(os.path.normpath(key))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return (directory or DEFAULT_LOCK_DIRECTORY) / f"ai-financial-assistant-mt5-{digest}.lock"


def _try_lock_exclusive(stream: IO[bytes]) -> bool:
    """Take an exclusive, non-blocking OS lock on ``stream``; False if taken.

    ``msvcrt`` (Windows) and ``fcntl`` (POSIX) are both advisory locks the kernel
    enforces between processes and releases when the process dies. The lock is
    one byte at offset 0: nothing in this project reads the file as data, it is
    only a rendezvous point for ownership.
    """
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(stream: IO[bytes]) -> None:
    """Release the lock taken by :func:`_try_lock_exclusive` (never raises)."""
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except Exception:  # teardown must not fail; closing the stream releases it too
        pass


class MT5OwnershipGuard:
    """Process-lifetime owner of ONE MT5 terminal (see the module docs).

    One instance per process, shared by every caller (the composition root builds
    it). ``acquire`` is refcounted, so nested or repeated application lifespans in
    the same process — which is how the test suite drives the app — neither
    deadlock on our own lock nor release it while another holder is still active.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._state_lock = threading.Lock()
        self._stream: IO[bytes] | None = None
        self._holders = 0

    @property
    def lock_path(self) -> Path:
        """The lock file this guard owns (diagnostics only; carries no secret)."""
        return self._lock_path

    @property
    def owned(self) -> bool:
        """Is this process currently holding the terminal ownership lock?"""
        with self._state_lock:
            return self._stream is not None

    def acquire(self) -> None:
        """Become (or re-enter as) the owner, or fail closed.

        Raises ``MT5OwnershipError`` when another process owns this terminal, or
        when the lock file cannot be created or locked at all — an ownership
        guarantee that cannot be established must never be assumed. Nothing here
        contacts MT5.
        """
        with self._state_lock:
            if self._stream is not None:
                self._holders += 1
                return
            stream = self._open()
            if not _try_lock_exclusive(stream):
                owner = self._describe_current_owner()
                self._close(stream)
                raise MT5OwnershipError(
                    "MT5 terminal ownership could not be established: another application "
                    f"process already owns this terminal (lock: {self._lock_path}{owner}). "
                    "MT5 state is process-global, so exactly one application process may "
                    "drive a terminal: run a single worker (no `uvicorn --workers`, no "
                    "gunicorn multi-worker) and stop any other running instance of this "
                    "application, or give this deployment its own terminal with "
                    "MT5_TERMINAL_PATH."
                )
            self._stream = stream
            self._holders = 1
            self._write_owner_record(stream)

    def release(self) -> None:
        """Give up one hold; the lock is released only when the last one ends.

        Never raises: teardown must not fail. Releasing the lock does not delete
        the file — the record of the last owner is useful to an operator, and the
        OS releases the lock itself when the process exits.
        """
        with self._state_lock:
            if self._stream is None:
                return
            self._holders -= 1
            if self._holders > 0:
                return
            stream, self._stream = self._stream, None
            self._holders = 0
            _unlock(stream)
            self._close(stream)

    # --- internals ---------------------------------------------------------

    def _open(self) -> IO[bytes]:
        """Open (creating if needed) the lock file, or fail closed.

        The file is only ever a rendezvous point: its content is the last owner's
        record, written after the lock is taken.
        """
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            return open(self._lock_path, "a+b")
        except OSError as exc:
            raise MT5OwnershipError(
                "MT5 terminal ownership could not be established: the ownership lock "
                f"file could not be opened (lock: {self._lock_path}). MT5 state is "
                "process-global and shared across processes, so this process refuses "
                "to start rather than risk serving one customer's data from another "
                "customer's account."
            ) from exc

    def _write_owner_record(self, stream: IO[bytes]) -> None:
        """Record this process as the owner, for operator diagnostics only."""
        try:
            record = f"pid={os.getpid()} terminal={_describe_terminal_key(self._lock_path)}\n".encode()
            stream.seek(0)
            stream.truncate(0)
            stream.write(record)
            stream.flush()
        except OSError:  # diagnostics must never block ownership
            pass

    def _describe_current_owner(self) -> str:
        """The existing owner's record, for the failure message (best effort)."""
        try:
            text = self._lock_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        return f"; current owner: {text}" if text else ""

    @staticmethod
    def _close(stream: IO[bytes]) -> None:
        try:
            stream.close()
        except OSError:  # pragma: no cover - closing a file we own does not fail
            pass


def _describe_terminal_key(lock_path: Path) -> str:
    """The lock's own file-name suffix, so the record names the terminal key."""
    return lock_path.stem
