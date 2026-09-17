"""Single-owner enforcement for the process-global MT5 terminal.

The session manager's lock is process-local, so nothing in it stops two OS
processes from driving the same terminal (which would break tenant isolation).
These tests cover the OS-level ownership guard that closes that gap: the normal
single-process case, the refusal when another owner holds the terminal, the
cross-process case, and what the real application does at startup.

Everything here is offline: the guard never touches MT5, and the "other owner"
is either a second file handle or a real child process, never a terminal.
"""
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.core.dependencies as deps
import app.core.mt5_ownership as ownership
from app.core.mt5_ownership import (
    MT5OwnershipError,
    MT5OwnershipGuard,
    ownership_lock_path,
)
from app.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# A stand-in for a real terminal location; only its identity matters here.
TERMINAL_A = r"C:\MT5-A\terminal64.exe"
TERMINAL_B = r"C:\MT5-B\terminal64.exe"


@pytest.fixture()
def lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the ownership lock at a per-test directory, and drop any live guard."""
    monkeypatch.setattr(ownership, "DEFAULT_LOCK_DIRECTORY", tmp_path, raising=True)
    deps.reset_mt5_ownership_guard()
    monkeypatch.setattr(deps, "_mt5_ownership_guard", None, raising=True)
    yield tmp_path
    deps.reset_mt5_ownership_guard()
    monkeypatch.setattr(deps, "_mt5_ownership_guard", None, raising=True)


# --- which terminal a lock belongs to ---------------------------------------


def test_the_lock_is_stable_for_one_terminal(lock_dir: Path) -> None:
    first = ownership_lock_path(TERMINAL_A, directory=lock_dir)
    again = ownership_lock_path(TERMINAL_A, directory=lock_dir)
    case_difference = ownership_lock_path(TERMINAL_A.lower(), directory=lock_dir)

    assert first == again == case_difference
    assert first.parent == lock_dir


def test_different_terminals_get_different_locks(lock_dir: Path) -> None:
    assert ownership_lock_path(TERMINAL_A, directory=lock_dir) != ownership_lock_path(
        TERMINAL_B, directory=lock_dir
    )


@pytest.mark.parametrize("unpinned", [None, "", "   "])
def test_an_unpinned_terminal_shares_one_lock(unpinned: str | None, lock_dir: Path) -> None:
    # No configured path means "let the package find the terminal": every such
    # process is a candidate owner of the same terminal.
    assert ownership_lock_path(unpinned, directory=lock_dir) == ownership_lock_path(
        None, directory=lock_dir
    )
    assert ownership_lock_path(unpinned, directory=lock_dir) != ownership_lock_path(
        TERMINAL_A, directory=lock_dir
    )


# --- the normal single-process case -----------------------------------------


def test_one_process_becomes_the_owner_and_releases_cleanly(lock_dir: Path) -> None:
    guard = MT5OwnershipGuard(ownership_lock_path(None, directory=lock_dir))

    assert guard.owned is False
    guard.acquire()
    assert guard.owned is True
    assert guard.lock_path.exists()

    guard.release()
    assert guard.owned is False


def test_acquire_is_refcounted_so_repeated_startups_do_not_deadlock(lock_dir: Path) -> None:
    guard = MT5OwnershipGuard(ownership_lock_path(None, directory=lock_dir))

    # Two overlapping holders in one process (how the suite drives lifespans).
    guard.acquire()
    guard.acquire()
    guard.release()
    assert guard.owned is True  # the first holder still owns it
    guard.release()
    assert guard.owned is False


def test_release_is_safe_when_nothing_was_acquired_or_released_twice(lock_dir: Path) -> None:
    guard = MT5OwnershipGuard(ownership_lock_path(None, directory=lock_dir))

    guard.release()  # never acquired
    guard.acquire()
    guard.release()
    guard.release()  # extra release

    assert guard.owned is False


def test_the_owner_is_recorded_for_the_operator(lock_dir: Path) -> None:
    guard = MT5OwnershipGuard(ownership_lock_path(TERMINAL_A, directory=lock_dir))
    guard.acquire()
    guard.release()

    # Readable once the lock is not held (while held, Windows denies the read).
    record = guard.lock_path.read_text(encoding="utf-8")
    assert "pid=" in record and "terminal=" in record
    assert "password" not in record.lower()


# --- refusing a second owner ------------------------------------------------


def test_a_second_owner_is_refused(lock_dir: Path) -> None:
    path = ownership_lock_path(None, directory=lock_dir)
    owner = MT5OwnershipGuard(path)
    owner.acquire()

    challenger = MT5OwnershipGuard(path)
    with pytest.raises(MT5OwnershipError) as excinfo:
        challenger.acquire()

    assert challenger.owned is False
    message = str(excinfo.value)
    # Explicit about the deployment fix, and about where the lock is.
    assert "another application process already owns this terminal" in message
    assert str(path) in message
    assert "MT5_TERMINAL_PATH" in message
    # No credential-ish content, and no MT5 internals.
    assert "password" not in message.lower()
    assert "MetaTrader5" not in message

    owner.release()
    # Once released, the same lock can be taken again (no stale lock to clear).
    challenger.acquire()
    assert challenger.owned is True
    challenger.release()


def test_owners_of_different_terminals_do_not_block_each_other(lock_dir: Path) -> None:
    one = MT5OwnershipGuard(ownership_lock_path(TERMINAL_A, directory=lock_dir))
    other = MT5OwnershipGuard(ownership_lock_path(TERMINAL_B, directory=lock_dir))

    one.acquire()
    other.acquire()

    assert (one.owned, other.owned) == (True, True)
    one.release()
    other.release()


def test_an_unusable_lock_location_fails_closed(lock_dir: Path) -> None:
    not_a_directory = lock_dir / "a-file"
    not_a_directory.write_text("not a directory", encoding="utf-8")
    guard = MT5OwnershipGuard(not_a_directory / "owner.lock")

    with pytest.raises(MT5OwnershipError) as excinfo:
        guard.acquire()

    assert guard.owned is False
    assert "could not be opened" in str(excinfo.value)


def test_the_guard_never_imports_or_calls_mt5() -> None:
    """Ownership is decided before any terminal exists: prove it structurally."""
    source = Path(ownership.__file__).read_text(encoding="utf-8")
    imports = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]

    # No MT5 module is imported anywhere in the guard, so no code path here can
    # reach a terminal (the session manager is the only MT5 importer).
    assert not [line for line in imports if "MetaTrader5" in line or "import mt5" in line]
    assert "MetaTrader5" not in source


# --- a REAL second process ---------------------------------------------------


_CHILD_SCRIPT = """
import sys
from pathlib import Path

sys.path.insert(0, {root!r})
from app.core.mt5_ownership import MT5OwnershipGuard, ownership_lock_path

guard = MT5OwnershipGuard(ownership_lock_path({key!r}, directory=Path({directory!r})))
guard.acquire()
print("OWNED", flush=True)
sys.stdin.readline()  # hold the lock until the parent closes stdin
guard.release()
print("RELEASED", flush=True)
"""


def _read_line_with_timeout(stream, timeout: float) -> str | None:
    """Read one line without ever hanging the suite."""
    box: queue.Queue[str] = queue.Queue()

    def read() -> None:
        box.put(stream.readline())

    threading.Thread(target=read, daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        return None


def _await_ownership(guard: MT5OwnershipGuard, *, timeout: float = 20.0) -> None:
    """Become the owner, waiting briefly for a dying owner's lock to clear.

    Windows releases a killed process's byte-range lock while the kernel tears
    the process down, which can trail ``Popen.wait()`` by a fraction of a second,
    so an immediate re-acquire can race it. Waiting for a BOUNDED time does not
    weaken what is asserted below - that the lock needs no manual cleanup - it
    only tolerates that the OS release is not instantaneous. A lock that is truly
    never released still fails, because the final attempt re-raises.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            guard.acquire()
            return
        except MT5OwnershipError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def test_another_process_owning_the_terminal_is_detected_and_released_on_exit(
    lock_dir: Path,
) -> None:
    """The real thing: a second OS process holds the lock, then is killed.

    This is the cross-process guarantee the in-process RLock cannot give, and it
    also shows the lock needs no cleanup: a killed owner's lock is gone.
    """
    directory = lock_dir
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT.format(root=str(PROJECT_ROOT), key=None, directory=str(directory))],
        cwd=str(PROJECT_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        line = _read_line_with_timeout(child.stdout, timeout=60)
        if line is None:
            child.kill()
            _, stderr = child.communicate(timeout=30)
            pytest.fail(f"child never took the lock (stderr: {stderr})")
        assert line.strip() == "OWNED"

        # While the child lives, this process must refuse to become the owner.
        guard = MT5OwnershipGuard(ownership_lock_path(None, directory=directory))
        with pytest.raises(MT5OwnershipError):
            guard.acquire()
        assert guard.owned is False
    finally:
        child.kill()
        child.wait(timeout=30)

    # Killed, not released: the OS drops the lock with the process, and nothing
    # here had to clear a stale lock by hand.
    after = MT5OwnershipGuard(ownership_lock_path(None, directory=directory))
    _await_ownership(after)
    assert after.owned is True
    after.release()


# --- the application boundary -------------------------------------------------


def test_the_application_starts_and_gives_up_ownership_on_shutdown(lock_dir: Path) -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        guard = deps.get_mt5_ownership_guard()
        assert guard.owned is True

    # After the lifespan exits the terminal is no longer owned, so a restart (or
    # another deployment) can take it.
    assert deps.get_mt5_ownership_guard().owned is False


def test_the_application_refuses_to_start_when_another_owner_holds_the_terminal(
    lock_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pre-existing owner of the same terminal (another handle/process).
    owner = MT5OwnershipGuard(ownership_lock_path(None, directory=lock_dir))
    owner.acquire()

    # Fail closed BEFORE any MT5 work: a session manager must never be built.
    def never_build_a_session_manager(*args: object, **kwargs: object) -> object:
        pytest.fail("no MT5 session may be created before terminal ownership is secured")

    monkeypatch.setattr(deps, "MT5SessionManager", never_build_a_session_manager, raising=True)

    try:
        with pytest.raises(MT5OwnershipError):
            with TestClient(app):
                pytest.fail("the app must not start while another process owns the terminal")
    finally:
        owner.release()
