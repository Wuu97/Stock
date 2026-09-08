from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List

from .models import Disposal, Lot, LotAllocation

MONEY = Decimal("0.0001")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def remaining_shares(lot: Lot, disposals: Iterable[Disposal]) -> int:
    disposed = sum(d.shares for d in disposals if d.lot_id == lot.lot_id)
    remaining = lot.orig_shares - disposed
    if remaining < 0:
        raise ValueError("lot disposals exceed original shares")
    return remaining


def fifo_allocate(lots: Iterable[Lot], disposals: Iterable[Disposal], sell_date: date,
                  requested_shares: int) -> List[LotAllocation]:
    if requested_shares <= 0:
        raise ValueError("requested shares must be positive")
    prior = tuple(disposals)
    eligible = sorted(
        (lot for lot in lots if lot.available_from_date <= sell_date),
        key=lambda lot: (lot.buy_trade_date, lot.lot_id),
    )
    need = requested_shares
    allocations: List[LotAllocation] = []
    for lot in eligible:
        if need == 0:
            break
        available = remaining_shares(lot, prior)
        quantity = min(available, need)
        if quantity:
            allocations.append(LotAllocation(lot.lot_id, quantity, lot.unit_cost,
                                              money(Decimal(quantity) * lot.unit_cost)))
            need -= quantity
    if need:
        raise ValueError("insufficient sellable shares under T+1/FIFO rules")
    return allocations
