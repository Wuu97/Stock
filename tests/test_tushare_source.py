from datetime import date
from decimal import Decimal

from quant_core.models import DayBar
from quant_core.tushare_source import (fetch_trading_status_rows, iter_trading_status_pages, merge_daily_limits,
                                       missing_limits, row_to_daily_limit, row_to_trading_status_event)


def _bar(day, ticker):
    return DayBar(day, ticker, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), 100, Decimal("1000"), None, None)


def test_tushare_limits_enrich_bars_without_mutating_originals():
    original = _bar(date(2024, 3, 4), "600000.SH")
    limit = row_to_daily_limit({"trade_date": "20240304", "ts_code": "600000.SH", "up_limit": 11, "down_limit": 9})
    enriched = merge_daily_limits([original], [limit])
    assert original.limit_up is None
    assert enriched[0].limit_up == Decimal("11")
    assert enriched[0].limit_down == Decimal("9")


def test_missing_limits_only_checks_required_tickers():
    bars = [_bar(date(2024, 3, 4), "600000.SH"), _bar(date(2024, 3, 4), "000300.SH")]
    assert missing_limits(bars, ["600000.SH"]) == ((date(2024, 3, 4), "600000.SH"),)
    assert missing_limits(bars, ["000001.SZ"]) == ()


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


class _TradingStatusClient:
    def __init__(self):
        self.calls = []

    def suspend_d(self, **kwargs):
        self.calls.append(("suspend_d", kwargs))
        return _Frame([{"ts_code": "600000.SH", "trade_date": "20260902", "suspend_type": "S"}])

    def query(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        offset = kwargs["offset"]
        pages = {
            0: [{"ts_code": "600000.SH", "trade_date": "20260902", "suspend_type": "S"}],
            1: [{"ts_code": "000001.SZ", "trade_date": "20260902", "suspend_type": "R"}],
            2: [],
        }
        return _Frame(pages[offset])


def test_trading_status_source_supports_precise_ticker_and_single_day_requests():
    client = _TradingStatusClient()
    start, end = date(2026, 9, 1), date(2026, 9, 3)
    assert fetch_trading_status_rows(client, start, end, "600000.SH")[0]["ts_code"] == "600000.SH"
    assert client.calls[-1][1]["ts_code"] == "600000.SH"
    fetch_trading_status_rows(client, date(2026, 9, 2), date(2026, 9, 2))
    assert client.calls[-1][1]["trade_date"] == "20260902"


def test_trading_status_source_iterates_full_market_pages_and_normalizes_events():
    client = _TradingStatusClient()
    pages = list(iter_trading_status_pages(client, date(2026, 9, 2), date(2026, 9, 2), 1))
    assert [offset for offset, _ in pages] == [0, 1]
    assert row_to_trading_status_event(pages[0][1][0]).status_code == "SUSPENDED"
    assert row_to_trading_status_event(pages[1][1][0]).status_code == "RESUMED"
