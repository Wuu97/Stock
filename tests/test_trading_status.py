from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from quant_core.trading_status import TradingStatusEvent, TradingStatusStore


def test_trading_status_store_returns_only_official_suspension_dates():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    TradingStatusStore(connection).store_snapshot(
        "status-snapshot", "tushare_suspend_d", date(2026, 9, 1), date(2026, 9, 3),
        "fixture.json", "a" * 64, now,
        (TradingStatusEvent("600000.SH", date(2026, 9, 2), "SUSPENDED"),
         TradingStatusEvent("600000.SH", date(2026, 9, 3), "RESUMED")), now,
    )

    assert TradingStatusStore(connection).suspended_tickers_by_date(
        "tushare_suspend_d", date(2026, 9, 1), date(2026, 9, 3)
    ) == {date(2026, 9, 2): {"600000.SH"}}


def test_trading_status_store_collapses_intraday_duplicates_conservatively():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    TradingStatusStore(connection).store_snapshot(
        "status-snapshot", "tushare_suspend_d", date(2026, 9, 2), date(2026, 9, 2),
        "fixture.json", "b" * 64, now,
        (TradingStatusEvent("600000.SH", date(2026, 9, 2), "RESUMED"),
         TradingStatusEvent("600000.SH", date(2026, 9, 2), "SUSPENDED", "09:30-09:40")), now,
    )

    assert connection.execute("SELECT status_code FROM trading_status_events").fetchone()[0] == "SUSPENDED"
