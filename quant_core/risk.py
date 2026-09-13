"""Pure, close-confirmed exit rules for daily-bar paper trading."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Optional, Protocol

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

    def evaluate(self, ticker: str, entry_price: Decimal, closes: list[DayBar]) -> Optional[ExitSignal]:
        return _evaluate_thresholds(ticker, entry_price, closes, self.stop_loss_rate, self.take_profit_min_rate,
                                    self.trailing_drawdown_rate, self.max_holding_days, "")


class ExitPolicy(Protocol):
    """Close-confirmed exit contract shared by paper trading and research."""

    def evaluate(self, ticker: str, entry_price: Decimal, closes: list[DayBar]) -> Optional[ExitSignal]:
        ...


@dataclass(frozen=True)
class VolatilityAdjustedExitRule:
    """Scale stop and trailing thresholds to observed holding-period volatility."""

    min_stop_loss_rate: Decimal = Decimal("0.06")
    max_stop_loss_rate: Decimal = Decimal("0.15")
    stop_volatility_multiple: Decimal = Decimal("2.5")
    take_profit_min_rate: Decimal = Decimal("0.15")
    min_trailing_drawdown_rate: Decimal = Decimal("0.03")
    max_trailing_drawdown_rate: Decimal = Decimal("0.10")
    trailing_volatility_multiple: Decimal = Decimal("1.5")
    volatility_lookback_days: int = 10
    max_holding_days: int = 60

    def __post_init__(self) -> None:
        rates = (self.min_stop_loss_rate, self.max_stop_loss_rate, self.take_profit_min_rate,
                 self.min_trailing_drawdown_rate, self.max_trailing_drawdown_rate)
        if not all(Decimal("0") < value < Decimal("1") for value in rates):
            raise ValueError("exit rates must be between zero and one")
        if (self.min_stop_loss_rate > self.max_stop_loss_rate
                or self.min_trailing_drawdown_rate > self.max_trailing_drawdown_rate
                or self.stop_volatility_multiple <= 0 or self.trailing_volatility_multiple <= 0
                or self.volatility_lookback_days < 2 or self.max_holding_days <= 0):
            raise ValueError("volatility exit parameters are invalid")

    def evaluate(self, ticker: str, entry_price: Decimal, closes: list[DayBar]) -> Optional[ExitSignal]:
        volatility = _close_return_volatility(closes, self.volatility_lookback_days)
        stop_loss = _clamp(self.stop_volatility_multiple * volatility,
                           self.min_stop_loss_rate, self.max_stop_loss_rate)
        trailing = _clamp(self.trailing_volatility_multiple * volatility,
                          self.min_trailing_drawdown_rate, self.max_trailing_drawdown_rate)
        return _evaluate_thresholds(ticker, entry_price, closes, stop_loss, self.take_profit_min_rate,
                                    trailing, self.max_holding_days, "VOLATILITY")


@dataclass(frozen=True)
class SignalDecayExitRule:
    """Exit when the holding's confirmed short-term close momentum decays."""

    stop_loss_rate: Decimal = Decimal("0.10")
    take_profit_min_rate: Decimal = Decimal("0.15")
    trailing_drawdown_rate: Decimal = Decimal("0.05")
    decay_lookback_days: int = 5
    decay_return_threshold: Decimal = Decimal("-0.03")
    min_holding_days: int = 5
    max_holding_days: int = 60

    def __post_init__(self) -> None:
        if not all(Decimal("0") < value < Decimal("1") for value in (
            self.stop_loss_rate, self.take_profit_min_rate, self.trailing_drawdown_rate,
        )):
            raise ValueError("exit rates must be between zero and one")
        if (self.decay_lookback_days < 2 or not Decimal("-1") < self.decay_return_threshold < Decimal("0")
                or self.min_holding_days < self.decay_lookback_days or self.max_holding_days <= 0):
            raise ValueError("signal-decay exit parameters are invalid")

    def evaluate(self, ticker: str, entry_price: Decimal, closes: list[DayBar]) -> Optional[ExitSignal]:
        signal = _evaluate_thresholds(ticker, entry_price, closes, self.stop_loss_rate,
                                      self.take_profit_min_rate, self.trailing_drawdown_rate,
                                      self.max_holding_days, "")
        if signal is not None:
            return signal
        if len(closes) < self.min_holding_days:
            return None
        short_return = (closes[-1].close / closes[-self.decay_lookback_days].close) - Decimal("1")
        if short_return <= self.decay_return_threshold:
            latest, peak = closes[-1].close, max(bar.close for bar in closes)
            return ExitSignal(ticker, "SIGNAL_DECAY_CLOSE", latest, peak, len(closes))
        return None


def evaluate_exit(ticker: str, entry_price: Decimal, closes_since_entry: Iterable[DayBar],
                  rule: ExitPolicy) -> Optional[ExitSignal]:
    """Evaluate end-of-day exits; a signal is always executed at the following open.

    Close confirmation deliberately avoids inventing an intraday stop/target ordering
    from a daily OHLC bar.
    """
    closes = [bar for bar in closes_since_entry if bar.ticker == ticker and bar.status == "TRADING"]
    if not closes:
        return None
    return rule.evaluate(ticker, entry_price, closes)


def _evaluate_thresholds(ticker: str, entry_price: Decimal, closes: list[DayBar], stop_loss_rate: Decimal,
                         take_profit_min_rate: Decimal, trailing_drawdown_rate: Decimal, max_holding_days: int,
                         reason_prefix: str) -> Optional[ExitSignal]:
    if not closes:
        return None
    latest, peak, holding_days = closes[-1].close, max(bar.close for bar in closes), len(closes)
    prefix = f"{reason_prefix}_" if reason_prefix else ""
    if latest <= entry_price * (Decimal("1") - stop_loss_rate):
        return ExitSignal(ticker, f"{prefix}STOP_LOSS_CLOSE", latest, peak, holding_days)
    if peak >= entry_price * (Decimal("1") + take_profit_min_rate) and latest <= peak * (
        Decimal("1") - trailing_drawdown_rate
    ):
        return ExitSignal(ticker, f"{prefix}TRAILING_TAKE_PROFIT_CLOSE", latest, peak, holding_days)
    if holding_days >= max_holding_days:
        return ExitSignal(ticker, f"{prefix}MAX_HOLDING_DAYS", latest, peak, holding_days)
    return None


def _close_return_volatility(closes: list[DayBar], lookback_days: int) -> Decimal:
    window = closes[-lookback_days:]
    returns = [(window[index].close / window[index - 1].close) - Decimal("1")
               for index in range(1, len(window))]
    if len(returns) < 2:
        return Decimal("0")
    mean = sum(returns) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns) - 1)
    return variance.sqrt()


def _clamp(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    return max(minimum, min(value, maximum))

