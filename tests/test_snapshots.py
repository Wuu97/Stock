from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from quant_core.snapshots import SnapshotService, canonical_hash, write_manifest


def _service(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    manifest = tmp_path / "manifest.json"
    digest = write_manifest(manifest, {"bars.parquet": "a" * 64})
    service = SnapshotService(con)
    published = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
    service.register_market_snapshot("market-1", date(2026, 9, 1), "fixture", published,
                                     published, str(manifest), digest, published)
    service.register_feature_snapshot("feature-1", date(2026, 9, 1), 20, "features.parquet",
                                      "b" * 64, ["market-1"], published)
    return con, service


def test_manifest_hash_is_stable_and_records_sorted_artifacts(tmp_path):
    first = write_manifest(tmp_path / "one.json", {"b": "2", "a": "1"})
    second = write_manifest(tmp_path / "two.json", {"a": "1", "b": "2"})
    assert first == second == canonical_hash({"artifacts": {"a": "1", "b": "2"}, "previous_hash": ""})


def test_freeze_run_blocks_data_not_ready_and_late_decision(tmp_path):
    con, service = _service(tmp_path)
    assert service.freeze_run("early", date(2026, 9, 2), "baseline", "v1", "cost_v1", "feature-1",
                              datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc), datetime.now(timezone.utc)) == "INVALID"
    assert service.freeze_run("late", date(2026, 9, 2), "baseline", "v1", "cost_v1", "feature-1",
                              datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc), datetime.now(timezone.utc)) == "INVALID"
    assert service.freeze_run("valid", date(2026, 9, 2), "baseline", "v1", "cost_v1", "feature-1",
                              datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc), datetime.now(timezone.utc)) == "FROZEN"
    rows = con.execute("SELECT run_id, run_status, invalid_reason_code FROM recommendation_runs ORDER BY run_id").fetchall()
    assert rows == [("early", "INVALID", "DATA_NOT_READY"), ("late", "INVALID", "LOOKAHEAD_VIOLATION"), ("valid", "FROZEN", None)]
