from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from scripts.run_local_daily_refresh import _completed_pipeline_snapshot, _publication_account_ids, _value_active_accounts


def _pipeline_fixture(connection, trade_date):
    now = datetime.now(timezone.utc)
    snapshot_id = f"tushare_pool_{trade_date:%Y%m%d}_limits"
    connection.execute("INSERT INTO sim_accounts VALUES ('acct', '账户', 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)", [now])
    connection.execute("INSERT INTO market_data_snapshots VALUES (?, ?, 'fixture', ?, ?, 'manifest', 'hash', ?)",
                       [snapshot_id, trade_date, now, now, now])
    connection.execute("INSERT INTO pipeline_runs VALUES ('pipeline', ?, 'acct', 'config', '{}', ?, 'COMPLETED', ?, ?, NULL)",
                       [trade_date, now, now, now])
    return snapshot_id


def test_local_refresh_requires_a_completed_pipeline_snapshot(tmp_path):
    db_path = tmp_path / "refresh.duckdb"
    connection = duckdb.connect(str(db_path))
    connection.execute(Path("sql/schema.sql").read_text())
    trade_date = date(2026, 9, 14)
    snapshot_id = _pipeline_fixture(connection, trade_date)
    connection.close()
    assert _completed_pipeline_snapshot(str(db_path), trade_date) == snapshot_id


def test_local_refresh_refuses_to_publish_before_pipeline_completion(tmp_path):
    db_path = tmp_path / "refresh_incomplete.duckdb"
    connection = duckdb.connect(str(db_path))
    connection.execute(Path("sql/schema.sql").read_text())
    trade_date = date(2026, 9, 14)
    _pipeline_fixture(connection, trade_date)
    connection.execute("UPDATE pipeline_runs SET run_status = 'RUNNING'")
    connection.close()
    with pytest.raises(ValueError, match="daily pipeline is not completed"):
        _completed_pipeline_snapshot(str(db_path), trade_date)


def test_daily_publication_account_scope_is_explicit(tmp_path):
    config = tmp_path / "monitors.json"
    config.write_text('{"accounts":[{"account_id":"personal","enabled":true},{"account_id":"disabled","enabled":false}]}')
    assert _publication_account_ids("forward", config) == ["forward", "personal"]


def test_daily_publication_does_not_value_unlisted_active_research_accounts(tmp_path):
    db_path = tmp_path / "scope.duckdb"
    connection = duckdb.connect(str(db_path)); connection.execute(Path("sql/schema.sql").read_text())
    now, day = datetime.now(timezone.utc), date(2026, 9, 15)
    connection.executemany("INSERT INTO sim_accounts VALUES (?, ?, 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)",
                           [("forward", "Forward", now), ("research", "Research", now)])
    connection.execute("INSERT INTO market_data_snapshots VALUES ('snap', ?, 'fixture', ?, ?, 'm', 'h', ?)", [day, now, now, now])
    connection.execute("INSERT INTO daily_bars VALUES ('snap', ?, 'AAA', 10, 10, 10, 10, 1, 10, NULL, NULL, 'TRADING')", [day])
    connection.close()
    assert _value_active_accounts(str(db_path), "snap", day, ["forward"]) == ["forward"]
