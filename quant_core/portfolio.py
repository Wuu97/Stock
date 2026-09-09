"""Pure, board-lot-aware portfolio construction for ranked recommendations."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, Optional, Tuple

from .lots import money
from .models import FeeModel
from .strategy import Recommendation


@dataclass(frozen=True)
class PortfolioPolicy:
    """Sizing policy only; score providers never decide position size."""

    method: str
    board_lot: int = 100
    max_positions: Optional[int] = None
    cash_reserve: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.method not in {"FIXED_SHARES", "EQUAL_WEIGHT"}:
            raise ValueError("unsupported portfolio method")
        if self.board_lot <= 0 or self.board_lot % 100:
            raise ValueError("board_lot must be a positive 100-share multiple")
        if self.max_positions is not None and self.max_positions <= 0:
            raise ValueError("max_positions must be positive when set")
        if not Decimal("0") <= self.cash_reserve < Decimal("1"):
            raise ValueError("cash_reserve must be between zero and one")

    @classmethod
    def fixed_shares(cls, shares: int, max_positions: Optional[int] = None) -> "PortfolioPolicy":
        if shares <= 0 or shares % 100:
            raise ValueError("fixed shares must be a positive board lot")
        return cls("FIXED_SHARES", shares, max_positions)

    @classmethod
    def equal_weight(cls, max_positions: int, cash_reserve: Decimal = Decimal("0")) -> "PortfolioPolicy":
        return cls("EQUAL_WEIGHT", 100, max_positions, cash_reserve)


@dataclass(frozen=True)
class PlannedBuy:
    ticker: str
    shares: int
    estimated_cash: Decimal


def construct_buys(recommendations: Iterable[Recommendation], policy: PortfolioPolicy, available_cash: Decimal,
                   fee: FeeModel, occupied_positions: int = 0) -> Tuple[PlannedBuy, ...]:
    """Return deterministic buy quantities using decision-time reference closes only."""
    if available_cash < 0 or occupied_positions < 0:
        raise ValueError("cash and occupied positions cannot be negative")
    candidates = tuple(recommendations)
    slots = len(candidates) if policy.max_positions is None else max(policy.max_positions - occupied_positions, 0)
    selected = candidates[:slots]
    if policy.method == "FIXED_SHARES":
        return tuple(PlannedBuy(item.ticker, policy.board_lot, _estimated_buy_cash(item.close, policy.board_lot, fee)) for item in selected)
    if not selected:
        return ()
    budget = money(available_cash * (Decimal("1") - policy.cash_reserve))
    per_position = budget / Decimal(len(selected))
    planned = []
    for item in selected:
        shares = _shares_within_budget(item.close, per_position, policy.board_lot, fee)
        if shares:
            planned.append(PlannedBuy(item.ticker, shares, _estimated_buy_cash(item.close, shares, fee)))
    return tuple(planned)


def _shares_within_budget(price: Decimal, budget: Decimal, board_lot: int, fee: FeeModel) -> int:
    if price <= 0 or budget <= 0:
        return 0
    gross_budget = budget - fee.min_commission
    denominator = price * (Decimal("1") + fee.commission_rate + fee.transfer_fee_rate)
    raw_shares = max(Decimal("0"), gross_budget / denominator)
    shares = int((raw_shares / board_lot).to_integral_value(rounding=ROUND_DOWN)) * board_lot
    while shares and _estimated_buy_cash(price, shares, fee) > budget:
        shares -= board_lot
    return shares


def _estimated_buy_cash(price: Decimal, shares: int, fee: FeeModel) -> Decimal:
    gross = money(Decimal(shares) * Decimal(str(price)))
    commission = max(money(gross * fee.commission_rate), fee.min_commission)
    return money(gross + commission + money(gross * fee.transfer_fee_rate))
