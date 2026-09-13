"""Project-wide cooperative single-writer lock for the local DuckDB file."""

from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

import fcntl


class WriterBusyError(RuntimeError):
    pass


@contextmanager
def exclusive_writer(db_path: str, wait_seconds: float = 0.0):
    """Serialize local writers before they attempt to open DuckDB."""
    lock_path = Path(str(db_path) + ".writer.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    deadline = monotonic() + wait_seconds
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise WriterBusyError("another local write task is running")
                sleep(min(0.5, max(0.0, deadline - monotonic())))
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
