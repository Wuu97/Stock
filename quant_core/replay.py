from decimal import Decimal
from typing import Iterable, Mapping, Tuple

from .lots import remaining_shares
from .models import Disposal, Lot


def replay_lots(lots: Iterable[Lot], disposals: Iterable[Disposal]) -> Mapping[str, Tuple[int, Decimal]]:
    events = tuple(disposals)
    result = {}
    for lot in lots:
        shares = remaining_shares(lot, events)
        prior_shares, prior_cost = result.get(lot.ticker, (0, Decimal("0.0000")))
        result[lot.ticker] = (prior_shares + shares, prior_cost + Decimal(shares) * lot.unit_cost)
    return result


def assert_balanced(entries) -> None:
    debit = sum((entry.debit for entry in entries), Decimal("0.0000"))
    credit = sum((entry.credit for entry in entries), Decimal("0.0000"))
    if debit != credit:
        raise AssertionError("journal replay imbalance")
