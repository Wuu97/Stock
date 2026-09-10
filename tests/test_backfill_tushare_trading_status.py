import importlib.util
from datetime import date
from pathlib import Path


def _script_module():
    path = Path("scripts/backfill_tushare_trading_status.py")
    spec = importlib.util.spec_from_file_location("backfill_tushare_trading_status", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unified_trading_status_cli_accepts_each_fetch_strategy():
    module = _script_module()
    parser = module.build_parser()
    ticker = parser.parse_args(["--db", "test.duckdb", "--ticker", "600000.SH", "--start-date", "2026-09-01", "--end-date", "2026-09-02"])
    single_day = parser.parse_args(["--db", "test.duckdb", "--trade-date", "2026-09-02"])
    all_market = parser.parse_args(["--db", "test.duckdb", "--all-market", "--start-date", "2026-09-01", "--end-date", "2026-09-02", "--page-size", "100"])
    assert ticker.ticker == ["600000.SH"]
    assert single_day.trade_date == ["2026-09-02"]
    assert all_market.all_market and all_market.page_size == 100
    assert module._requests(ticker.ticker, None, date(2026, 9, 1), date(2026, 9, 2)) == [
        ("600000_SH", date(2026, 9, 1), date(2026, 9, 2), "600000.SH")
    ]
    assert module._requests(None, single_day.trade_date, None, None) == [
        ("all", date(2026, 9, 2), date(2026, 9, 2), None)
    ]
