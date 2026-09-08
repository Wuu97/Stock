from datetime import date
from decimal import Decimal

from quant_core.models import Disposal, Lot
from quant_core.replay import replay_lots


def test_replay_rebuilds_holdings_from_immutable_events():
    lot = Lot("lot-1", "acc", "600519.SH", date(2026, 9, 1), date(2026, 9, 2), 1000, Decimal("100"))
    result = replay_lots([lot], [
        Disposal("lot-1", date(2026, 9, 3), 300),
        Disposal("lot-1", date(2026, 9, 4), 300),
    ])
    assert result == {"600519.SH": (400, Decimal("40000.0000"))}
