from pathlib import Path

import duckdb
import pytest

from quant_core.research_snapshot import assert_research_database, create_research_snapshot


def test_research_snapshot_uses_duckdb_export_import_and_marks_target(tmp_path):
    production = tmp_path / "production.duckdb"
    connection = duckdb.connect(str(production))
    connection.execute("CREATE TABLE facts(value INTEGER)")
    connection.execute("INSERT INTO facts VALUES (7)")
    connection.close()
    target = tmp_path / "research.duckdb"
    result = create_research_snapshot(production, target, tmp_path / "artifacts")
    connection = duckdb.connect(str(target))
    try:
        assert connection.execute("SELECT value FROM facts").fetchone()[0] == 7
        assert_research_database(connection)
        assert connection.execute("SELECT snapshot_id FROM research_snapshot_metadata").fetchone()[0] == result["snapshot_id"]
    finally:
        connection.close()


def test_research_snapshot_refuses_to_overwrite_an_existing_study_database(tmp_path):
    production, target = tmp_path / "production.duckdb", tmp_path / "research.duckdb"
    duckdb.connect(str(production)).close()
    target.touch()
    with pytest.raises(ValueError, match="already exists"):
        create_research_snapshot(production, target, tmp_path / "artifacts")
