from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from scripts.daily_pipeline import _taxonomy_version, _tracked_tickers
from quant_core.pipeline_audit import PipelineStore
from quant_core.cost_models import load_cost_model


def test_pipeline_tracks_pending_and_held_symbols_and_uses_latest_taxonomy():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('SW2021_V1', '1', '行业', 3)")
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('SW2021_V2', '2', '行业', 3)")
    connection.execute("INSERT INTO sim_accounts VALUES ('acct', '账户', 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)", [now])
    connection.execute(
        "INSERT INTO sim_order_intents VALUES ('pending', NULL, 'acct', '000001.SZ', ?, 'SELL', 100, 'NEXT_OPEN_WITH_SLIPPAGE', 'PENDING', NULL, ?)",
        [date(2026, 9, 10), now],
    )
    connection.execute(
        "INSERT INTO sim_position_lots VALUES ('lot', 'acct', '600000.SH', 'execution', ?, ?, 100, 10, ?)",
        [date(2026, 9, 1), date(2026, 9, 2), now],
    )
    assert _tracked_tickers(connection, "acct") == ["000001.SZ", "600000.SH"]
    assert _taxonomy_version(connection, None) == "SW2021_V2"
    assert _taxonomy_version(connection, "PINNED") == "PINNED"


def test_cost_model_loader_uses_the_versioned_project_config():
    fee = load_cost_model("cost_a_share_2026_v1", Path("config/cost_models.yaml"))
    assert fee.version == "cost_a_share_2026_v1"
    assert fee.commission_rate == Decimal("0.00025")


def test_pipeline_audit_reuses_successful_stage_and_records_non_blocking_failure(tmp_path):
    db_path = tmp_path / "pipeline.duckdb"
    connection = duckdb.connect(str(db_path))
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO sim_accounts VALUES ('acct', '账户', 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)", [now])
    connection.close()
    store = PipelineStore(str(db_path), tmp_path / "artifacts")
    run_id, _, completed = store.start_or_resume(date(2026, 9, 10), "acct", {"group": "top50"}, now)
    assert not completed
    assert store.run_stage(run_id, "refresh", "BLOCKING", lambda: {"snapshot": "one"}) == {"snapshot": "one"}
    assert store.run_stage(run_id, "refresh", "BLOCKING", lambda: (_ for _ in ()).throw(RuntimeError("must not rerun"))) == {"snapshot": "one"}
    failure = store.run_stage(run_id, "news", "NON_BLOCKING", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    assert failure["status"] == "NON_BLOCKING_FAILURE"
    store.finish(run_id)
    connection = duckdb.connect(str(db_path), read_only=True)
    assert connection.execute("SELECT run_status FROM pipeline_runs WHERE pipeline_run_id = ?", [run_id]).fetchone()[0] == "COMPLETED_WITH_WARNINGS"
    assert connection.execute("SELECT attempt_number, stage_status FROM pipeline_run_stage_attempts WHERE pipeline_run_id = ? AND stage_name = 'news'", [run_id]).fetchall() == [(1, 'FAILED')]
    connection.close()


def test_blocking_stage_failure_resumes_with_a_new_append_only_attempt(tmp_path):
    db_path = tmp_path / "resume.duckdb"
    connection = duckdb.connect(str(db_path)); connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO sim_accounts VALUES ('acct', '账户', 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)", [now]); connection.close()
    store = PipelineStore(str(db_path), tmp_path / "artifacts")
    run_id, _, _ = store.start_or_resume(date(2026, 9, 10), "acct", {"version": 1}, now)
    with pytest.raises(RuntimeError, match="first failure"):
        store.run_stage(run_id, "strategy", "BLOCKING", lambda: (_ for _ in ()).throw(RuntimeError("first failure")))
    resumed_id, cutoff, completed = store.start_or_resume(date(2026, 9, 10), "acct", {"version": 1}, now)
    assert resumed_id == run_id and cutoff == now and not completed
    assert store.run_stage(run_id, "strategy", "BLOCKING", lambda: {"run": "ok"}) == {"run": "ok"}
    connection = duckdb.connect(str(db_path), read_only=True)
    assert connection.execute("SELECT attempt_number, stage_status FROM pipeline_run_stage_attempts WHERE pipeline_run_id = ? AND stage_name = 'strategy' ORDER BY attempt_number", [run_id]).fetchall() == [(1, "FAILED"), (2, "SUCCEEDED")]
    connection.close()


def test_completed_run_fast_returns_and_configuration_changes_fail_closed(tmp_path):
    db_path = tmp_path / "complete.duckdb"
    connection = duckdb.connect(str(db_path)); connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO sim_accounts VALUES ('acct', '账户', 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)", [now]); connection.close()
    store = PipelineStore(str(db_path), tmp_path / "artifacts")
    run_id, _, _ = store.start_or_resume(date(2026, 9, 10), "acct", {"cost": "hash-a"}, now)
    store.run_stage(run_id, "settlement", "BLOCKING", lambda: {"orders": 1})
    store.finish(run_id)
    assert store.start_or_resume(date(2026, 9, 10), "acct", {"cost": "hash-a"}, now)[2]
    with pytest.raises(ValueError, match="configuration differs"):
        store.start_or_resume(date(2026, 9, 10), "acct", {"cost": "hash-b"}, now)
