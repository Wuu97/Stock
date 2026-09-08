from datetime import date
from decimal import Decimal

from quant_core.models import DayBar
from quant_core.tushare_source import merge_daily_limits, missing_limits, row_to_daily_limit


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
