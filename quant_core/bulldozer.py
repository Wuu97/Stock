"""Faithful daily-bar research proxy for the documented ``推土机战法``.

This module intentionally models only rules that are explicit and observable from
completed daily bars.  Auction microstructure, subjective chart "beauty", market
themes, and intraday MACD/TD exits remain outside this proxy.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from .models import DayBar


@dataclass(frozen=True)
class BulldozerConfig:
    """Frozen, documented daily rules; unspecified source rules are not invented."""

    top_n: int
    consecutive_ma5_days: int = 8

    def __post_init__(self) -> None:
        if self.top_n <= 0:
            raise ValueError("bulldozer top_n must be positive")
        if self.consecutive_ma5_days < 1:
            raise ValueError("bulldozer consecutive_ma5_days must be positive")


@dataclass(frozen=True)
class BulldozerFeatureRow:
    ticker: str
    as_of_trade_date: date
    close: Decimal
    ma5: Decimal
    close_above_ma5_run_length: int
    bias_ma5: Decimal


def build_bulldozer_features(bars: Iterable[DayBar], as_of_trade_date: date,
                              config: BulldozerConfig) -> list[BulldozerFeatureRow]:
    """Build point-in-time features using no bar later than ``as_of_trade_date``."""
    by_ticker: dict[str, list[DayBar]] = {}
    for bar in bars:
        if bar.status == "TRADING" and bar.trade_date <= as_of_trade_date:
            by_ticker.setdefault(bar.ticker, []).append(bar)
    rows = []
    required = config.consecutive_ma5_days + 4
    for ticker, history in by_ticker.items():
        history = sorted(history, key=lambda item: item.trade_date)
        if len(history) < required:
            continue
        closes = [item.close for item in history]
        ma5_values = [sum(closes[index - 4:index + 1], Decimal("0")) / Decimal("5")
                      for index in range(4, len(closes))]
        run = 0
        for close, ma5 in zip(reversed(closes[4:]), reversed(ma5_values)):
            if close >= ma5:
                run += 1
            else:
                break
        ma5 = ma5_values[-1]
        rows.append(BulldozerFeatureRow(
            ticker=ticker,
            as_of_trade_date=as_of_trade_date,
            close=closes[-1],
            ma5=ma5,
            close_above_ma5_run_length=run,
            bias_ma5=(closes[-1] / ma5) - Decimal("1"),
        ))
    return rows


def build_bulldozer_features_by_day(bars: Sequence[DayBar], calendar: Sequence[date],
                                    config: BulldozerConfig) -> dict[date, list[BulldozerFeatureRow]]:
    """Convenience cache for daily replay; every row remains point-in-time."""
    return {day: build_bulldozer_features(bars, day, config) for day in calendar}
