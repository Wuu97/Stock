"""Pure daily-bar matching rules. This module never reads or writes the database."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .lots import money
from .models import DayBar, ExecutionResult, FeeModel, OrderIntent


@dataclass(frozen=True)
class OpenGapPolicy:
    """Frozen, daily-frequency entry guard using only T-1 close and T open."""

    max_gap_up: Decimal = Decimal("0.03")
    max_gap_down: Decimal = Decimal("-0.04")

    def __post_init__(self) -> None:
        if self.max_gap_up < 0 or self.max_gap_down >= 0:
            raise ValueError("open gap bounds must straddle zero")


def match_next_open(intent: OrderIntent, bar: DayBar, fee: FeeModel,
                    reference_close: Optional[Decimal] = None,
                    open_gap_policy: Optional[OpenGapPolicy] = None) -> ExecutionResult:
    """Return an all-or-nothing daily-bar execution assumption for one valid intent."""
    if intent.direction not in {"BUY", "SELL"} or intent.shares <= 0 or intent.shares % 100:
        return ExecutionResult(False, "INVALID_ORDER")
    if intent.ticker != bar.ticker or intent.target_trade_date != bar.trade_date:
        return ExecutionResult(False, "BAR_MISMATCH")
    if bar.status != "TRADING" or bar.volume <= 0 or bar.amount <= 0 or bar.open <= 0:
        return ExecutionResult(False, "SUSPENDED")
    if bar.limit_up is None or bar.limit_down is None:
        return ExecutionResult(False, "DATA_MISSING_PRICE_LIMIT")
    if bar.open == bar.high == bar.low == bar.limit_up and intent.direction == "BUY":
        return ExecutionResult(False, "LIMIT_UP_BARRIER")
    if bar.open == bar.high == bar.low == bar.limit_down and intent.direction == "SELL":
        return ExecutionResult(False, "LIMIT_DOWN_BARRIER")
    gap_rejection = _open_gap_rejection(intent, bar, reference_close, open_gap_policy)
    if gap_rejection:
        return ExecutionResult(False, gap_rejection)

    multiplier = Decimal("1") + fee.slippage_rate if intent.direction == "BUY" else Decimal("1") - fee.slippage_rate
    raw_price = money(bar.open * multiplier)
    price = min(raw_price, bar.high) if intent.direction == "BUY" else max(raw_price, bar.low)
    price = money(price)
    gross = money(price * intent.shares)
    commission = max(money(gross * fee.commission_rate), fee.min_commission)
    stamp_duty = money(gross * fee.stamp_duty_rate) if intent.direction == "SELL" else Decimal("0")
    transfer_fee = money(gross * fee.transfer_fee_rate)
    return ExecutionResult(
        accepted=True,
        price=price,
        gross_amount=gross,
        commission=commission,
        stamp_duty=stamp_duty,
        transfer_fee=transfer_fee,
        price_cap_applied=price != raw_price,
    )


def _open_gap_rejection(intent: OrderIntent, bar: DayBar, reference_close: Optional[Decimal],
                        policy: Optional[OpenGapPolicy]) -> Optional[str]:
    """Do not apply a buy-entry guard to sell intents or legacy callers without a policy."""
    if policy is None or intent.direction != "BUY":
        return None
    if reference_close is None or reference_close <= 0:
        return "DATA_MISSING_REFERENCE_CLOSE"
    opening_gap = (bar.open / reference_close) - Decimal("1")
    if opening_gap > policy.max_gap_up:
        return "OPEN_GAP_UP_TOO_HIGH"
    if opening_gap < policy.max_gap_down:
        return "OPEN_GAP_DOWN_TOO_LOW"
    return None
