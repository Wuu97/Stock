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
    momentum_5d: Decimal = Decimal("0")
    macd: Decimal = None
    macd_signal: Decimal = None
    previous_macd: Decimal = None
    kdj_k: Decimal = None
    kdj_d: Decimal = None
    kdj_j: Decimal = None


def build_features(bars: Iterable[DayBar], as_of_trade_date: date, lookback_days: int) -> List[FeatureRow]:
    if lookback_days < 2:
        raise ValueError("lookback_days must be at least two")
    by_ticker: Dict[str, List[DayBar]] = {}
    for bar in bars:
        if bar.trade_date <= as_of_trade_date and bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    rows = []
    for ticker, history in by_ticker.items():
        ordered = sorted(history, key=lambda item: item.trade_date)
        window = ordered[-lookback_days:]
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
            momentum_5d=((window[-1].close / window[-6].close) - Decimal("1")
                          if lookback_days >= 6 else Decimal("0")),
            **_technical_values(ordered),
        ))
    return rows


def _technical_values(history: List[DayBar]) -> dict:
    """Daily-only MACD/KDJ values; every value uses bars at or before as-of."""
    closes = [bar.close for bar in history]
    if len(closes) < 2:
        return {}
    ema12, ema26, signal = closes[0], closes[0], Decimal("0")
    macds = []
    for close in closes:
        ema12 += (close - ema12) * Decimal(2) / Decimal(13)
        ema26 += (close - ema26) * Decimal(2) / Decimal(27)
        macd = ema12 - ema26
        signal += (macd - signal) * Decimal(2) / Decimal(10)
        macds.append(macd)
    k = d = Decimal("50")
    for index in range(8, len(history)):
        window = history[index - 8:index + 1]
        high, low = max(bar.high for bar in window), min(bar.low for bar in window)
        rsv = Decimal("50") if high == low else (history[index].close - low) * Decimal("100") / (high - low)
        k = Decimal(2) * k / Decimal(3) + rsv / Decimal(3)
        d = Decimal(2) * d / Decimal(3) + k / Decimal(3)
    values = {"macd": macds[-1], "macd_signal": signal, "previous_macd": macds[-2]}
    if len(history) >= 9:
        values.update({"kdj_k": k, "kdj_d": d, "kdj_j": Decimal(3) * k - Decimal(2) * d})
    return values
