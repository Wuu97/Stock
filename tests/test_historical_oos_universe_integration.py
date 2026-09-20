"""Frozen-fixture integration coverage for the OOS evidence-to-Universe boundary."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import duckdb
import pytest

from quant_core.st_history_migrations import apply_st_history_migrations


_SPEC = importlib.util.spec_from_file_location(
    "backfill_historical_universes", Path("scripts/backfill_historical_universes.py")
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_SOURCE = "tushare_oos2020_history_daily_v1"


def _fixture_db(tmp_path, *, missing_st_index=None):
    db, calendar_path = tmp_path / "fixture.duckdb", tmp_path / "calendar.json"
    connection = duckdb.connect(str(db))
    connection.execute(Path("sql/schema.sql").read_text())
    apply_st_history_migrations(connection)
    now, first = datetime.now(timezone.utc), date(2020, 1, 1)
    days = [first + timedelta(days=index) for index in range(61)]
    for day in days:
        key = day.strftime("%Y%m%d")
        connection.execute("INSERT INTO market_data_snapshots VALUES (?, ?, ?, ?, ?, 'fixture', 'hash', ?)",
                           [f"oos2020_tushare_market_{key}", day, _SOURCE, now, now, now])
        connection.execute("INSERT INTO daily_bars VALUES (?, ?, 'AAA.SZ', 10, 11, 9, ?, 100, 1000, 12, 8, 'TRADING')",
                           [f"oos2020_tushare_market_{key}", day, Decimal(10 + (day - first).days)])
        connection.execute("INSERT INTO market_cap_snapshots VALUES (?, ?, ?, ?)",
                           [f"oos2020_tushare_cap_{key}", now, _SOURCE, now])
        connection.execute("INSERT INTO market_cap_values VALUES (?, 'AAA.SZ', ?)",
                           [f"oos2020_tushare_cap_{key}", Decimal("90000000000")])
    connection.execute("INSERT INTO security_listing_snapshots VALUES ('listing', 'historical_listing_fact_reference_v1', 'x', 'h', ?, ?)", [now, now])
    connection.execute("INSERT INTO security_listing_values VALUES ('listing', 'AAA.SZ', ?, NULL, 'L')", [first])
    requested = days[30:]
    connection.execute("INSERT INTO st_history_backfill_runs VALUES ('st', 'baostock', ?, ?, 'baostock_is_st_supplier_fact_v1', ?)",
                       [requested[0], requested[-1], now])
    for day in requested:
        if day != (requested[missing_st_index] if missing_st_index is not None else None):
            connection.execute("INSERT INTO st_history_daily VALUES ('st', 'AAA.SZ', ?, false, 'raw', 'hash', ?)", [day, now])
    connection.close()
    payload = {"reference_type": "HISTORICAL_TRADING_CALENDAR_REFERENCE", "reference_version": "historical_trading_calendar_reference_v1",
               "source": "tushare_trade_cal_sse_open_days_v1", "exchange": "SSE", "start_date": first.isoformat(),
               "end_date": days[-1].isoformat(), "trading_days": [value.isoformat() for value in days]}
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    calendar_path = tmp_path / f"historical_trading_calendar_reference_{digest[:20]}.json"
    calendar_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return db, calendar_path, requested


def _run(monkeypatch, db, calendar, requested, group="fixture_oos"):
    monkeypatch.setattr(sys, "argv", ["backfill_historical_universes.py", "--db", str(db), "--start-date", requested[0].isoformat(),
        "--end-date", requested[-1].isoformat(), "--group-name", group, "--listing-snapshot-id", "listing",
        "--st-backfill-run-id", "st", "--calendar-reference", str(calendar)])
    _MODULE.main()


def test_frozen_oos_evidence_builds_universe_without_discovery_source(monkeypatch, tmp_path):
    db, calendar, requested = _fixture_db(tmp_path)
    _run(monkeypatch, db, calendar, requested)
    connection = duckdb.connect(str(db), read_only=True)
    assert connection.execute("SELECT count(*) FROM universe_snapshots WHERE group_name='fixture_oos'").fetchone()[0] == len(requested)
    assert connection.execute("SELECT count(*) FROM universe_snapshots WHERE group_name='fixture_oos' AND market_cap_snapshot_id LIKE 'tushare_history%'").fetchone()[0] == 0
    connection.close()


def test_missing_st_fails_before_any_universe_write(monkeypatch, tmp_path):
    db, calendar, requested = _fixture_db(tmp_path, missing_st_index=0)
    with pytest.raises(ValueError, match="ST history is missing"):
        _run(monkeypatch, db, calendar, requested)
    connection = duckdb.connect(str(db), read_only=True)
    assert connection.execute("SELECT count(*) FROM universe_snapshots").fetchone()[0] == 0
    connection.close()


def test_mid_range_failure_rolls_back_all_universe_writes(monkeypatch, tmp_path):
    db, calendar, requested = _fixture_db(tmp_path, missing_st_index=1)
    with pytest.raises(ValueError, match="ST history is missing"):
        _run(monkeypatch, db, calendar, requested)
    connection = duckdb.connect(str(db), read_only=True)
    assert connection.execute("SELECT count(*) FROM universe_snapshots").fetchone()[0] == 0
    connection.close()


def test_discovery_market_source_is_rejected_before_database_access(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["backfill_historical_universes.py", "--db", str(tmp_path / "absent.duckdb"),
        "--start-date", "2020-01-01", "--end-date", "2020-01-01", "--group-name", "g",
        "--listing-snapshot-id", "listing", "--st-backfill-run-id", "st", "--calendar-reference", "absent.json",
        "--market-source", "tushare_history_daily"])
    with pytest.raises(ValueError, match="never Discovery"):
        _MODULE.main()
    assert not (tmp_path / "absent.duckdb").exists()
