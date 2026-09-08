"""Pure, close-confirmed exit rules for daily-bar paper trading."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional

from .models import DayBar, ExitSignal


@dataclass(frozen=True)
class ExitRule:
    """Uniform rules; all thresholds are positive decimal rates, e.g. 0.08 for 8%."""
    stop_loss_rate: Decimal = Decimal("0.10")
    take_profit_min_rate: Decimal = Decimal("0.15")
    trailing_drawdown_rate: Decimal = Decimal("0.05")
    max_holding_days: int = 60

    def __post_init__(self) -> None:
        if not all(Decimal("0") < value < Decimal("1") for value in (
            self.stop_loss_rate, self.take_profit_min_rate, self.trailing_drawdown_rate,
        )):
            raise ValueError("exit rates must be between zero and one")
        if self.max_holding_days <= 0:
            raise ValueError("max_holding_days must be positive")


def evaluate_exit(ticker: str, entry_price: Decimal, closes_since_entry: Iterable[DayBar],
                  rule: ExitRule) -> Optional[ExitSignal]:
    """Evaluate end-of-day exits; a signal is always executed at the following open.

    Close confirmation deliberately avoids inventing an intraday stop/target ordering
    from a daily OHLC bar.
    """
    closes = [bar.close for bar in closes_since_entry if bar.ticker == ticker and bar.status == "TRADING"]
    if not closes:
        return None
    latest, peak = closes[-1], max(closes)
    holding_days = len(closes)
    if latest <= entry_price * (Decimal("1") - rule.stop_loss_rate):
        return ExitSignal(ticker, "STOP_LOSS_CLOSE", latest, peak, holding_days)
    if peak >= entry_price * (Decimal("1") + rule.take_profit_min_rate) and latest <= peak * (
        Decimal("1") - rule.trailing_drawdown_rate
    ):
        return ExitSignal(ticker, "TRAILING_TAKE_PROFIT_CLOSE", latest, peak, holding_days)
    if holding_days >= rule.max_holding_days:
        return ExitSignal(ticker, "MAX_HOLDING_DAYS", latest, peak, holding_days)
    return None
