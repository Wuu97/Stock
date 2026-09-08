"""Pure daily-bar matching rules. This module never reads or writes the database."""

from decimal import Decimal

from .lots import money
from .models import DayBar, ExecutionResult, FeeModel, OrderIntent


def match_next_open(intent: OrderIntent, bar: DayBar, fee: FeeModel) -> ExecutionResult:
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
