from datetime import date
from decimal import Decimal

import pytest

from quant_core.calendar import TradingCalendar
from quant_core.lots import fifo_allocate
from quant_core.models import Disposal, Lot


def test_fifo_respects_t_plus_one_and_allocates_oldest_lot_first():
    calendar = TradingCalendar([date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)])
    old = Lot("lot-old", "acc", "600519.SH", date(2026, 9, 1), calendar.next_trading_day(date(2026, 9, 1)), 300, Decimal("10"))
    new = Lot("lot-new", "acc", "600519.SH", date(2026, 9, 2), calendar.next_trading_day(date(2026, 9, 2)), 500, Decimal("12"))
    allocations = fifo_allocate([old, new], [], date(2026, 9, 2), 200)
    assert [(item.lot_id, item.shares) for item in allocations] == [("lot-old", 200)]
    with pytest.raises(ValueError, match="T\\+1"):
        fifo_allocate([old, new], [], date(2026, 9, 2), 400)


def test_fifo_accounts_for_prior_partial_disposals():
    lot = Lot("lot-1", "acc", "000001.SZ", date(2026, 9, 1), date(2026, 9, 2), 500, Decimal("10"))
    allocations = fifo_allocate([lot], [Disposal("lot-1", date(2026, 9, 2), 200)], date(2026, 9, 3), 300)
    assert allocations[0].total_cost == Decimal("3000.0000")
