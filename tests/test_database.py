import duckdb
import pytest

from quant_core.database import read_connection, writer_connection


def test_writer_connection_commits_and_read_connection_is_read_only(tmp_path):
    path = str(tmp_path / "prices.duckdb")
    with writer_connection(path) as connection:
        connection.execute("CREATE TABLE prices (value INTEGER)")
        connection.execute("INSERT INTO prices VALUES (7)")
    with read_connection(path) as connection:
        assert connection.execute("SELECT value FROM prices").fetchall() == [(7,)]
        with pytest.raises(duckdb.InvalidInputException):
            connection.execute("INSERT INTO prices VALUES (8)")


def test_writer_connection_rolls_back_on_error(tmp_path):
    path = str(tmp_path / "prices.duckdb")
    with duckdb.connect(path) as connection:
        connection.execute("CREATE TABLE prices (value INTEGER)")
    with pytest.raises(RuntimeError):
        with writer_connection(path) as connection:
            connection.execute("INSERT INTO prices VALUES (7)")
            raise RuntimeError("stop")
    with read_connection(path) as connection:
        assert connection.execute("SELECT * FROM prices").fetchall() == []


def test_non_transactional_writer_connection_releases_lock_when_closed(tmp_path):
    path = str(tmp_path / "prices.duckdb")
    with writer_connection(path, transaction=False) as connection:
        connection.execute("CREATE TABLE prices (value INTEGER)")
    with writer_connection(path, wait_seconds=0) as connection:
        connection.execute("INSERT INTO prices VALUES (7)")
