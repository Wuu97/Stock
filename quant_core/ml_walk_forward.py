"""Leak-free walk-forward evaluation for the deployable Ridge ML shadow model."""

from math import sqrt
from typing import Iterable, Mapping, Sequence

from .ml_shadow import FEATURE_COLUMNS


def rank_ic(predictions: Sequence[float], actuals: Sequence[float]):
    if len(predictions) < 2:
        return None
    x, y = _ranks(predictions), _ranks(actuals)
    x_mean, y_mean = sum(x) / len(x), sum(y) / len(y)
    denominator = sqrt(sum((value - x_mean) ** 2 for value in x) * sum((value - y_mean) ** 2 for value in y))
    return (sum((left - x_mean) * (right - y_mean) for left, right in zip(x, y)) / denominator
            if denominator else None)


def walk_forward_ridge(rows: Iterable[Mapping], train_window_days: int = 480, test_window_days: int = 60,
                       window_policy: str = "rolling", top_n: int = 5, ridge_alpha: float = 1.0,
                       baseline_volume_multiple: float = 1.5) -> dict:
    """Train strictly before every out-of-sample window and return daily ranking evidence."""
    if train_window_days < 1 or test_window_days < 1 or top_n < 1 or ridge_alpha < 0:
        raise ValueError("walk-forward parameters must be positive; ridge alpha may be zero")
    if window_policy not in {"rolling", "expanding"}:
        raise ValueError("window policy must be rolling or expanding")
    frame = sorted((dict(row) for row in rows if row["label_status"] == "MATURE"),
                   key=lambda row: (row["trade_date"], row["ticker"]))
    dates = sorted({row["trade_date"] for row in frame})
    periods, daily = [], []
    for start in range(train_window_days, len(dates), test_window_days):
        train_start = 0 if window_policy == "expanding" else start - train_window_days
        train_dates, test_dates = dates[train_start:start], dates[start:start + test_window_days]
        if not test_dates:
            continue
        train = [row for row in frame if row["trade_date"] in train_dates]
        test = [dict(row) for row in frame if row["trade_date"] in test_dates]
        model = fit_ridge(train, ridge_alpha)
        for row in test:
            row["prediction"] = predict_ridge(model, row)
        period_daily = []
        for trade_date in test_dates:
            cross_section = [row for row in test if row["trade_date"] == trade_date]
            ic = rank_ic([row["prediction"] for row in cross_section],
                         [row["target_excess_ret_5d"] for row in cross_section])
            top_model = sorted(cross_section, key=lambda row: (-row["prediction"], row["ticker"]))[:top_n]
            qualified = [row for row in cross_section if row["sma20_deviation"] > 0
                         and row["volume_ratio_20d"] >= baseline_volume_multiple]
            top_rule = sorted(qualified, key=lambda row: (-row["momentum_20d"], row["ticker"]))[:top_n]
            item = {
                "trade_date": str(trade_date), "rank_ic": ic,
                "model_top_excess_return_gross": _mean(row["target_excess_ret_5d"] for row in top_model),
                "baseline_top_excess_return_gross": _mean(row["target_excess_ret_5d"] for row in top_rule),
                "model_picks": [row["ticker"] for row in top_model],
                "baseline_picks": [row["ticker"] for row in top_rule],
            }
            daily.append(item)
            period_daily.append(item)
        periods.append({
            "train_dates": [str(train_dates[0]), str(train_dates[-1])],
            "test_dates": [str(test_dates[0]), str(test_dates[-1])],
            "training_rows": len(train), "rank_ic_mean": _mean(row["rank_ic"] for row in period_daily),
            "model_top_excess_return_gross_mean": _mean(row["model_top_excess_return_gross"] for row in period_daily),
            "baseline_top_excess_return_gross_mean": _mean(row["baseline_top_excess_return_gross"] for row in period_daily),
        })
    if not periods:
        raise ValueError("dataset does not contain enough dates for one walk-forward period")
    return {"feature_columns": list(FEATURE_COLUMNS), "window_policy": window_policy,
            "train_window_days": train_window_days, "test_window_days": test_window_days,
            "top_n": top_n, "ridge_alpha": ridge_alpha, "baseline_volume_multiple": baseline_volume_multiple,
            "periods": periods, "daily_oos": daily,
            "rank_ic_mean": _mean(row["rank_ic"] for row in daily),
            "model_top_excess_return_gross_mean": _mean(row["model_top_excess_return_gross"] for row in daily),
            "baseline_top_excess_return_gross_mean": _mean(row["baseline_top_excess_return_gross"] for row in daily)}


def fit_ridge(rows: Sequence[Mapping], ridge_alpha: float) -> dict:
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("install the ML extra before running: python -m pip install -e '.[ml]'") from error
    if not rows:
        raise ValueError("ridge model requires training rows")
    matrix = np.asarray([[row[name] for name in FEATURE_COLUMNS] for row in rows], dtype=float)
    labels = np.asarray([row["target_excess_ret_5d"] for row in rows], dtype=float)
    means, scales = matrix.mean(axis=0), matrix.std(axis=0)
    scales[scales == 0] = 1.0
    normalized = (matrix - means) / scales
    design = np.column_stack((np.ones(len(normalized)), normalized))
    penalty = np.eye(design.shape[1]) * ridge_alpha
    penalty[0, 0] = 0.0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ labels)
    return {"means": dict(zip(FEATURE_COLUMNS, means)), "scales": dict(zip(FEATURE_COLUMNS, scales)),
            "coefficients": dict(zip(FEATURE_COLUMNS, weights[1:])), "intercept": float(weights[0])}


def predict_ridge(model: Mapping, row: Mapping) -> float:
    return float(model["intercept"] + sum(
        model["coefficients"][name] * ((float(row[name]) - model["means"][name]) / model["scales"][name])
        for name in FEATURE_COLUMNS
    ))


def _mean(values):
    usable = [float(value) for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _ranks(values):
    order = sorted(range(len(values)), key=lambda index: values[index])
    result, cursor = [0.0] * len(values), 0
    while cursor < len(order):
        end = cursor
        while end + 1 < len(order) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + end + 2) / 2
        for index in order[cursor:end + 1]:
            result[index] = average
        cursor = end + 1
    return result
