# Presence of this file puts the project root on sys.path so tests can `import app`.
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

import app.core.mt5_ownership as mt5_ownership


@pytest.fixture(scope="session", autouse=True)
def _isolated_mt5_ownership_lock() -> Iterator[None]:
    """Keep the suite off the developer's real MT5 terminal ownership lock.

    The app enforces "exactly one process owns a terminal" with an exclusive OS
    lock held for the process lifetime (app/core/mt5_ownership.py). The suite boots
    the real application in a TestClient many times over, so with the default lock
    location it would present itself as a second owner of the developer's terminal
    whenever a development server (`uvicorn app.main:app`) is already running —
    and correct, healthy workstations would then fail the suite.

    Tests never drive a real terminal (MT5 is mocked at the composition-root seams,
    and no test requires the terminal, credentials or a live account), so they take
    their ownership lock from a private directory instead. The guard itself is
    unaffected: the single-owner behavior, the refusal, and the application-startup
    enforcement are still exercised — just on a lock no other process shares. The
    directory lasts for the whole session, so the guard built lazily at the first
    app startup keeps one stable lock path.
    """
    original = mt5_ownership.DEFAULT_LOCK_DIRECTORY
    with tempfile.TemporaryDirectory(prefix="mt5-ownership-tests-") as directory:
        mt5_ownership.DEFAULT_LOCK_DIRECTORY = Path(directory)
        try:
            yield
        finally:
            mt5_ownership.DEFAULT_LOCK_DIRECTORY = original
