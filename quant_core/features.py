"""Small, deterministic daily features for the first rule-based baseline."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List

from .models import DayBar


@dataclass(frozen=True)
class FeatureRow:
    ticker: str
    as_of_trade_date: date
    close: Decimal
    sma: Decimal
    average_volume: Decimal
    momentum: Decimal
    volume_ratio: Decimal


def build_features(bars: Iterable[DayBar], as_of_trade_date: date, lookback_days: int) -> List[FeatureRow]:
    if lookback_days < 2:
        raise ValueError("lookback_days must be at least two")
    by_ticker: Dict[str, List[DayBar]] = {}
    for bar in bars:
        if bar.trade_date <= as_of_trade_date and bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    rows = []
    for ticker, history in by_ticker.items():
        window = sorted(history, key=lambda item: item.trade_date)[-lookback_days:]
        if len(window) != lookback_days:
            continue
        closes = [item.close for item in window]
        volumes = [item.volume for item in window]
        sma = sum(closes, Decimal("0")) / lookback_days
        average_volume = Decimal(sum(volumes)) / lookback_days
        rows.append(FeatureRow(
            ticker=ticker,
            as_of_trade_date=as_of_trade_date,
            close=window[-1].close,
            sma=sma,
            average_volume=average_volume,
            momentum=(window[-1].close / window[0].close) - Decimal("1"),
            volume_ratio=Decimal(window[-1].volume) / average_volume if average_volume else Decimal("0"),
        ))
    return rows
