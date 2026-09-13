"""Consistent, short-lived access helpers for the local DuckDB files.

DuckDB does not coordinate writes from separate processes.  All writers in this
project therefore acquire the per-database cooperative lock *before* opening a
read/write connection.  Keep vendor requests and other slow work outside
``writer_connection`` so the critical section contains only the database work.
"""

from contextlib import contextmanager
from typing import Iterator

import duckdb

from .writer_lock import exclusive_writer


@contextmanager
def read_connection(db_path: str) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open a connection that cannot accidentally take a database write lock."""
    connection = duckdb.connect(db_path, read_only=True)
    try:
        yield connection
    finally:
        connection.close()


class _WriterConnection:
    """Locked DuckDB connection; use it as a context manager whenever possible."""

    def __init__(self, db_path: str, wait_seconds: float, transaction: bool) -> None:
        self._lock = exclusive_writer(db_path, wait_seconds=wait_seconds)
        self._lock.__enter__()
        self._transaction = transaction
        self._closed = False
        try:
            self._connection = duckdb.connect(db_path)
            if transaction:
                self._connection.execute("BEGIN TRANSACTION")
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._transaction:
                self._connection.execute("ROLLBACK")
            self._connection.close()
        finally:
            self._closed = True
            self._lock.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._transaction:
                self._connection.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._transaction = False
            self.close()


def writer_connection(db_path: str, *, wait_seconds: float = 30.0,
                      transaction: bool = True) -> _WriterConnection:
    """Open the sole supported write connection for a DuckDB database file.

    With the default transaction, use ``with`` to commit on success and roll
    back on error. Set ``transaction=False`` only for legacy services that
    explicitly manage their own transaction, and always close that connection.
    """
    return _WriterConnection(db_path, wait_seconds, transaction)
