"""Walk-forward evaluation of K-line signals without assuming trade execution."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, Sequence, Tuple

from .features import build_features
from .models import DayBar
from .strategy import BaselineConfig, select_baseline


@dataclass(frozen=True)
class SignalObservation:
    as_of_trade_date: date
    ticker: str
    rank: int
    score: Decimal
    horizon_days: int
    future_trade_date: date
    close_return: Decimal


def run_baseline_signal_study(bars: Iterable[DayBar], eligible_tickers: Iterable[str],
                              start_date: date, end_date: date, lookback_days: int,
                              config: BaselineConfig, horizons: Sequence[int] = (1, 5, 20)) -> Tuple[SignalObservation, ...]:
    """Evaluate only returns available after each historical signal date."""
    eligible = set(eligible_tickers)
    selected_bars = [bar for bar in bars if bar.ticker in eligible and bar.status == "TRADING"]
    by_ticker: Dict[str, list[DayBar]] = {}
    for bar in selected_bars:
        by_ticker.setdefault(bar.ticker, []).append(bar)
    for history in by_ticker.values():
        history.sort(key=lambda bar: bar.trade_date)
    position_by_day = {
        ticker: {bar.trade_date: index for index, bar in enumerate(history)}
        for ticker, history in by_ticker.items()
    }
    decision_days = sorted({bar.trade_date for bar in selected_bars if start_date <= bar.trade_date <= end_date})
    observations = []
    for as_of_date in decision_days:
        features = build_features(selected_bars, as_of_date, lookback_days)
        picks = select_baseline(features, config)
        for pick in picks:
            history = by_ticker[pick.ticker]
            position = position_by_day[pick.ticker][as_of_date]
            for horizon in horizons:
                if position + horizon >= len(history):
                    continue
                future = history[position + horizon]
                observations.append(SignalObservation(
                    as_of_date, pick.ticker, pick.rank, Decimal(str(pick.score)), horizon,
                    future.trade_date, (future.close / Decimal(str(pick.close))) - Decimal("1"),
                ))
    return tuple(observations)


def summarize_signal_study(observations: Iterable[SignalObservation]) -> dict:
    grouped: Dict[int, list[SignalObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.horizon_days, []).append(observation)
    return {
        f"T+{horizon}": {
            "signals": len(rows),
            "positive_signals": sum(row.close_return > 0 for row in rows),
            "win_rate": Decimal(sum(row.close_return > 0 for row in rows)) / len(rows) if rows else Decimal("0"),
            "mean_close_return": sum((row.close_return for row in rows), Decimal("0")) / len(rows) if rows else Decimal("0"),
        }
        for horizon, rows in sorted(grouped.items())
    }
