from datetime import date
from decimal import Decimal

from quant_core.matching import match_next_open
from quant_core.models import DayBar, FeeModel, OrderIntent


FEE = FeeModel("cost_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0.01"))


def test_matching_caps_price_within_daily_range():
    intent = OrderIntent("i", "a", "600000.SH", date(2026, 9, 2), "BUY", 100)
    bar = DayBar(date(2026, 9, 2), "600000.SH", Decimal("10"), Decimal("10.05"), Decimal("9.90"), Decimal("10"), 10, Decimal("1000"), Decimal("11"), Decimal("9"))
    result = match_next_open(intent, bar, FEE)
    assert result.accepted and result.price == Decimal("10.0500") and result.price_cap_applied


def test_matching_rejects_one_price_limit_up_buy():
    intent = OrderIntent("i", "a", "600000.SH", date(2026, 9, 2), "BUY", 100)
    bar = DayBar(date(2026, 9, 2), "600000.SH", Decimal("11"), Decimal("11"), Decimal("11"), Decimal("11"), 10, Decimal("1000"), Decimal("11"), Decimal("9"))
    assert match_next_open(intent, bar, FEE).reason == "LIMIT_UP_BARRIER"


def test_matching_refuses_bars_without_vendor_price_limits():
    intent = OrderIntent("i", "a", "600000.SH", date(2026, 9, 2), "BUY", 100)
    bar = DayBar(date(2026, 9, 2), "600000.SH", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"), 10, Decimal("1000"), None, None)
    assert match_next_open(intent, bar, FEE).reason == "DATA_MISSING_PRICE_LIMIT"
