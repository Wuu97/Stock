from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class Lot:
    lot_id: str
    account_id: str
    ticker: str
    buy_trade_date: date
    available_from_date: date
    orig_shares: int
    unit_cost: Decimal


@dataclass(frozen=True)
class Disposal:
    lot_id: str
    trade_date: date
    shares: int


@dataclass(frozen=True)
class LotAllocation:
    lot_id: str
    shares: int
    unit_cost: Decimal
    total_cost: Decimal


@dataclass(frozen=True)
class JournalEntry:
    account_code: str
    debit: Decimal
    credit: Decimal
    memo: str
    ticker: Optional[str] = None


@dataclass(frozen=True)
class FeeModel:
    version: str
    commission_rate: Decimal
    min_commission: Decimal
    stamp_duty_rate: Decimal
    transfer_fee_rate: Decimal
    slippage_rate: Decimal


@dataclass(frozen=True)
class DayBar:
    trade_date: date
    ticker: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal
    limit_up: Optional[Decimal]
    limit_down: Optional[Decimal]
    status: str = "TRADING"


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    account_id: str
    ticker: str
    target_trade_date: date
    direction: str
    shares: int
    recommendation_item_id: Optional[str] = None


@dataclass(frozen=True)
class ExecutionResult:
    accepted: bool
    reason: Optional[str] = None
    price: Optional[Decimal] = None
    gross_amount: Optional[Decimal] = None
    commission: Decimal = Decimal("0")
    stamp_duty: Decimal = Decimal("0")
    transfer_fee: Decimal = Decimal("0")
    price_cap_applied: bool = False


@dataclass(frozen=True)
class ExitSignal:
    """A close-confirmed instruction to submit a sell order on the next trading day."""
    ticker: str
    reason: str
    reference_close: Decimal
    peak_close: Decimal
    holding_days: int
