"""Causally safe cross-sectional data sets for offline ML research only."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from typing import Iterable, Mapping, Optional, Sequence

from .models import DayBar


class SnapshotMissingError(ValueError):
    """A required point-in-time universe or input observation is absent."""


class BenchmarkIncompleteError(ValueError):
    """The benchmark cannot support an exact forward-return label."""


@dataclass(frozen=True)
class DatasetRow:
    trade_date: date
    ticker: str
    close: float
    momentum_5d: float
    momentum_20d: float
    sma20_deviation: float
    volume_ratio_20d: float
    momentum_20d_percentile: float
    momentum_20d_zscore: float
    target_excess_ret_5d: Optional[float]
    label_status: str

    def as_dict(self) -> dict:
        return asdict(self) | {"trade_date": self.trade_date.isoformat()}


def build_cross_sectional_dataset(
    bars: Iterable[DayBar], calendar: Sequence[date], universe_by_date: Mapping[date, set[str]],
    adjusted_closes: Mapping[tuple[date, str], Decimal], benchmark_adjusted_closes: Mapping[date, Decimal],
    start_date: date, end_date: date, lookback_days: int = 20, horizon_days: int = 5,
) -> list[DatasetRow]:
    """Build only mature labels; every usable decision cross-section is PIT-strict.

    Raw bars supply features.  Adjusted closes are an explicit external input used
    solely for research labels, so corporate actions never become price signals.
    """
    if lookback_days < 6 or horizon_days < 1:
        raise ValueError("lookback_days must be at least six and horizon_days at least one")
    days = sorted(set(calendar))
    if len(days) != len(calendar):
        raise ValueError("calendar contains duplicate dates")
    day_index = {day: index for index, day in enumerate(days)}
    by_ticker: dict[str, list[DayBar]] = {}
    for bar in bars:
        if bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    for history in by_ticker.values():
        history.sort(key=lambda bar: bar.trade_date)

    decision_days = [
        day for day in days
        if start_date <= day <= end_date
        and day_index[day] >= lookback_days - 1
        and day_index[day] + horizon_days < len(days)
    ]
    rows: list[DatasetRow] = []
    for trade_date in decision_days:
        if trade_date not in universe_by_date:
            raise SnapshotMissingError(f"point-in-time universe snapshot is missing for: {trade_date.isoformat()}")
        future_date = days[day_index[trade_date] + horizon_days]
        benchmark_start = benchmark_adjusted_closes.get(trade_date)
        benchmark_end = benchmark_adjusted_closes.get(future_date)
        if benchmark_start is None or benchmark_end is None:
            raise BenchmarkIncompleteError(
                f"benchmark adjusted close is missing for: {trade_date.isoformat()} or {future_date.isoformat()}"
            )
        benchmark_return = (benchmark_end / benchmark_start) - Decimal("1")
        raw_rows = []
        for ticker in sorted(universe_by_date[trade_date]):
            history = [bar for bar in by_ticker.get(ticker, []) if bar.trade_date <= trade_date]
            if len(history) < lookback_days:
                raise SnapshotMissingError(f"raw feature history is incomplete for {ticker} on {trade_date.isoformat()}")
            window = history[-lookback_days:]
            if window[-1].trade_date != trade_date:
                raise SnapshotMissingError(f"raw feature bar is missing for {ticker} on {trade_date.isoformat()}")
            adjusted_start = adjusted_closes.get((trade_date, ticker))
            adjusted_end = adjusted_closes.get((future_date, ticker))
            if adjusted_start is None:
                raise SnapshotMissingError(f"adjusted close is missing for {ticker} on {trade_date.isoformat()}")
            closes = [bar.close for bar in window]
            volumes = [bar.volume for bar in window]
            average_volume = Decimal(sum(volumes)) / len(volumes)
            raw_rows.append({
                "ticker": ticker,
                "close": float(closes[-1]),
                "momentum_5d": float((closes[-1] / closes[-6]) - Decimal("1")),
                "momentum_20d": float((closes[-1] / closes[0]) - Decimal("1")),
                "sma20_deviation": float((closes[-1] / (sum(closes) / len(closes))) - Decimal("1")),
                "volume_ratio_20d": float(Decimal(volumes[-1]) / average_volume) if average_volume else 0.0,
                "target_excess_ret_5d": float(((adjusted_end / adjusted_start) - Decimal("1")) - benchmark_return) if adjusted_end else None,
                "label_status": "MATURE" if adjusted_end else "UNTRADEABLE_OUTCOME",
            })
        percentile, zscore = _cross_section_statistics([row["momentum_20d"] for row in raw_rows])
        rows.extend(DatasetRow(trade_date=trade_date, momentum_20d_percentile=percentile[index],
                               momentum_20d_zscore=zscore[index], **row)
                    for index, row in enumerate(raw_rows))
    return rows


def _cross_section_statistics(values: Sequence[float]) -> tuple[list[float], list[float]]:
    if not values:
        return [], []
    ordered = sorted(values)
    percentile = [(sum(value >= candidate for candidate in ordered) - 0.5) / len(values) for value in values]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    scale = sqrt(variance)
    return percentile, [(value - mean) / scale if scale else 0.0 for value in values]
