"""Model-agnostic, deterministic out-of-sample prediction diagnostics."""

from dataclasses import dataclass
from math import log, sqrt
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class RegressionObservation:
    prediction: float
    actual: float


@dataclass(frozen=True)
class ProbabilityObservation:
    probability: float
    actual: bool


@dataclass(frozen=True)
class RiskObservation:
    predicted_drawdown: float
    actual_drawdown: float


def regression_metrics(observations: Iterable[RegressionObservation]) -> dict:
    rows = tuple(observations)
    if not rows:
        return {"count": 0, "mae": None, "rmse": None, "pooled_rank_correlation": None}
    errors = [row.prediction - row.actual for row in rows]
    return {
        "count": len(rows),
        "mae": sum(abs(value) for value in errors) / len(errors),
        "rmse": sqrt(sum(value * value for value in errors) / len(errors)),
        # This pools observations across dates.  It is not a daily cross-sectional Rank IC.
        "pooled_rank_correlation": rank_correlation([row.prediction for row in rows], [row.actual for row in rows]),
    }


def newey_west_mean_interval(values: Sequence[float], lags: int = 5) -> dict:
    """Two-sided normal 95% interval for a time-series mean using Bartlett HAC."""
    if lags < 0:
        raise ValueError("lags must be non-negative")
    rows = [float(value) for value in values]
    if not rows:
        return {"count": 0, "mean": None, "lags": lags, "standard_error": None, "lower_95": None, "upper_95": None}
    mean = sum(rows) / len(rows)
    if len(rows) == 1:
        return {"count": 1, "mean": mean, "lags": 0, "standard_error": None, "lower_95": None, "upper_95": None}
    effective_lags = min(lags, len(rows) - 1)
    centered = [value - mean for value in rows]
    variance = sum(value * value for value in centered) / len(rows)
    for lag in range(1, effective_lags + 1):
        covariance = sum(centered[index] * centered[index - lag] for index in range(lag, len(rows))) / len(rows)
        variance += 2 * (1 - lag / (effective_lags + 1)) * covariance
    standard_error = sqrt(max(0.0, variance) / len(rows))
    return {"count": len(rows), "mean": mean, "lags": effective_lags, "standard_error": standard_error,
            "lower_95": mean - 1.96 * standard_error, "upper_95": mean + 1.96 * standard_error}


def daily_rank_ic_metrics(values: Sequence[float], hac_lags: int = 5) -> dict:
    """Aggregate daily cross-sectional Rank IC; HAC accounts for serial overlap only."""
    interval = newey_west_mean_interval(values, hac_lags)
    return {"daily_count": interval.pop("count"), "daily_rank_ic_mean": interval.pop("mean"),
            "daily_rank_ic_hac_95": interval}


def probability_metrics(observations: Iterable[ProbabilityObservation], bins: int = 10) -> dict:
    rows = tuple(observations)
    if bins < 2:
        raise ValueError("bins must be at least two")
    if any(not 0 <= row.probability <= 1 for row in rows):
        raise ValueError("probabilities must be between zero and one")
    if not rows:
        return {"count": 0, "brier": None, "log_loss": None, "calibration": []}
    eps = 1e-15
    calibration = []
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        bucket = [row for row in rows if lower <= row.probability < upper or index == bins - 1 and row.probability == 1]
        if bucket:
            calibration.append({"lower": lower, "upper": upper, "count": len(bucket),
                                "mean_prediction": sum(row.probability for row in bucket) / len(bucket),
                                "observed_frequency": sum(row.actual for row in bucket) / len(bucket)})
    return {
        "count": len(rows),
        "brier": sum((row.probability - float(row.actual)) ** 2 for row in rows) / len(rows),
        "log_loss": -sum(float(row.actual) * log(max(row.probability, eps)) +
                         (1 - float(row.actual)) * log(max(1 - row.probability, eps)) for row in rows) / len(rows),
        "calibration": calibration,
    }


def drawdown_risk_metrics(observations: Iterable[RiskObservation], thresholds: Sequence[float]) -> dict:
    rows = tuple(observations)
    if any(row.predicted_drawdown < 0 or row.actual_drawdown < 0 for row in rows):
        raise ValueError("drawdowns must be non-negative")
    if any(threshold < 0 for threshold in thresholds):
        raise ValueError("thresholds must be non-negative")
    result = {"count": len(rows), "mae": None if not rows else sum(
        abs(row.predicted_drawdown - row.actual_drawdown) for row in rows) / len(rows), "thresholds": []}
    for threshold in sorted(set(thresholds)):
        predicted_high = [row for row in rows if row.predicted_drawdown >= threshold]
        actual_high = [row for row in rows if row.actual_drawdown >= threshold]
        true_positive = [row for row in predicted_high if row.actual_drawdown >= threshold]
        result["thresholds"].append({
            "threshold": threshold, "predicted_high_count": len(predicted_high), "actual_high_count": len(actual_high),
            "precision": None if not predicted_high else len(true_positive) / len(predicted_high),
            "recall": None if not actual_high else len(true_positive) / len(actual_high),
        })
    return result


def rank_correlation(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    if len(left) != len(right):
        raise ValueError("rank correlation inputs must have equal length")
    if len(left) < 2:
        return None
    x, y = _average_ranks(left), _average_ranks(right)
    x_mean, y_mean = sum(x) / len(x), sum(y) / len(y)
    denominator = sqrt(sum((value - x_mean) ** 2 for value in x) * sum((value - y_mean) ** 2 for value in y))
    return None if denominator == 0 else sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y)) / denominator


def _average_ranks(values: Sequence[float]) -> list:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result, cursor = [0.0] * len(values), 0
    while cursor < len(order):
        end = cursor
        while end + 1 < len(order) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        rank = (cursor + end + 2) / 2
        for index in order[cursor:end + 1]:
            result[index] = rank
        cursor = end + 1
    return result
