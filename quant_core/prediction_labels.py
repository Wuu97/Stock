"""Point-in-time-safe labels for return, direction, and path-risk research."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping, Optional, Sequence


LABEL_SCHEMA_VERSION = "prediction_label_v1"
MATURE = "MATURE"
UNMATURED = "UNMATURED"
UNTRADEABLE_OUTCOME = "UNTRADEABLE_OUTCOME"


@dataclass(frozen=True)
class PredictionTarget:
    """One versioned outcome definition, known only at its horizon close."""

    horizon_days: int
    upward_return_threshold: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.horizon_days < 1:
            raise ValueError("horizon_days must be positive")
        if self.upward_return_threshold <= Decimal("-1"):
            raise ValueError("upward_return_threshold must be greater than -1")

    @property
    def target_id(self) -> str:
        return f"close_to_close_t{self.horizon_days}_v1"


@dataclass(frozen=True)
class PredictionLabel:
    target: PredictionTarget
    decision_trade_date: date
    label_available_trade_date: Optional[date]
    status: str
    absolute_return: Optional[Decimal]
    excess_return: Optional[Decimal]
    is_up: Optional[bool]
    max_close_drawdown: Optional[Decimal]


def build_prediction_label(*, target: PredictionTarget, calendar: Sequence[date], decision_trade_date: date,
                           ticker: str, adjusted_closes: Mapping[tuple[date, str], Decimal],
                           benchmark_adjusted_closes: Mapping[date, Decimal]) -> PredictionLabel:
    """Build an outcome without allowing absent future data to masquerade as zero.

    Drawdown is the largest peak-to-trough decline in adjusted closes from the
    decision close through the horizon close, inclusive.  It is an outcome
    label, never a decision-time risk estimate.
    """
    days = tuple(calendar)
    if len(set(days)) != len(days) or tuple(sorted(days)) != days:
        raise ValueError("calendar must be unique and sorted")
    try:
        decision_index = days.index(decision_trade_date)
    except ValueError as error:
        raise ValueError("decision_trade_date is absent from calendar") from error
    end_index = decision_index + target.horizon_days
    if end_index >= len(days):
        return PredictionLabel(target, decision_trade_date, None, UNMATURED, None, None, None, None)
    end_date = days[end_index]
    path_dates = days[decision_index:end_index + 1]
    stock_path = [adjusted_closes.get((day, ticker)) for day in path_dates]
    benchmark_path = [benchmark_adjusted_closes.get(day) for day in (decision_trade_date, end_date)]
    if any(value is None for value in stock_path) or any(value is None for value in benchmark_path):
        return PredictionLabel(target, decision_trade_date, None, UNTRADEABLE_OUTCOME, None, None, None, None)
    if any(value <= 0 for value in stock_path) or any(value <= 0 for value in benchmark_path):
        raise ValueError("adjusted closes must be positive")
    absolute_return = (stock_path[-1] / stock_path[0]) - Decimal("1")
    benchmark_return = (benchmark_path[-1] / benchmark_path[0]) - Decimal("1")
    return PredictionLabel(
        target, decision_trade_date, end_date, MATURE, absolute_return,
        absolute_return - benchmark_return, absolute_return > target.upward_return_threshold,
        _max_close_drawdown(stock_path),
    )


def _max_close_drawdown(closes: Sequence[Decimal]) -> Decimal:
    peak, drawdown = closes[0], Decimal("0")
    for close in closes:
        peak = max(peak, close)
        drawdown = max(drawdown, (peak - close) / peak)
    return drawdown
