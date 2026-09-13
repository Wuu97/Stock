from datetime import date
from decimal import Decimal

from quant_core.etf_source import fund_daily_to_bars


def test_fund_daily_bars_are_research_only_without_derived_price_limits():
    bars = fund_daily_to_bars(({
        "ts_code": "512400.SH", "trade_date": "20260911", "open": "1.70", "high": "1.73", "low": "1.69",
        "close": "1.71", "vol": "12", "amount": "20",
    },))
    assert bars[0].ticker == "512400.SH"
    assert bars[0].trade_date == date(2026, 9, 11)
    assert bars[0].amount == Decimal("20000")
    assert bars[0].limit_up is None and bars[0].limit_down is None
