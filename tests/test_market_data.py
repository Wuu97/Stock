from datetime import date
from decimal import Decimal

import pytest

from quant_core.market_data import validate_bar
from quant_core.models import DayBar


def _bar(**overrides):
    values = {
        "trade_date": date(2026, 9, 8), "ticker": "600000.SH", "open": Decimal("10"),
        "high": Decimal("11"), "low": Decimal("9"), "close": Decimal("10.5"), "volume": 100,
        "amount": Decimal("1000"), "limit_up": Decimal("11"), "limit_down": Decimal("9"), "status": "TRADING",
    }
    values.update(overrides)
    return DayBar(**values)


def test_market_data_rejects_one_sided_or_invalid_vendor_limits_without_derivation():
    with pytest.raises(ValueError, match="incomplete price limits"):
        validate_bar(_bar(limit_down=None))
    with pytest.raises(ValueError, match="invalid price limits"):
        validate_bar(_bar(limit_up=Decimal("8")))


def test_market_data_rejects_invalid_ohlc_relationships():
    with pytest.raises(ValueError, match="invalid trading OHLC"):
        validate_bar(_bar(high=Decimal("9")))
