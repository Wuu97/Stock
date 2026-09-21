"""Causal, research-only walk-forward diagnostics for multi-horizon labels."""

from math import exp
from typing import Iterable, Mapping

from .ml_shadow import FEATURE_COLUMNS
from .ml_walk_forward import fit_ridge, predict_ridge
from .prediction_evaluation import (ProbabilityObservation, RegressionObservation,
                                    RiskObservation, drawdown_risk_metrics,
                                    newey_west_mean_interval, probability_metrics, regression_metrics)


def walk_forward_multitarget(rows: Iterable[Mapping], horizons=(5, 10, 20), train_window_days=480,
                             test_window_days=60, window_policy="rolling", ridge_alpha=1.0) -> dict:
    """Evaluate separate return, up-probability and drawdown models per horizon.

    A date joins a training window only once *all retained rows* have an outcome
    available on or before the preceding decision date.  The logistic model is
    deliberately a small linear baseline, not a trading recommendation.
    """
    if train_window_days < 1 or test_window_days < 1 or ridge_alpha < 0:
        raise ValueError("window sizes must be positive and ridge alpha non-negative")
    if window_policy not in {"rolling", "expanding"}:
        raise ValueError("window policy must be rolling or expanding")
    result = {"schema_version": "p1_multitarget_walk_forward_v1", "feature_columns": list(FEATURE_COLUMNS),
              "train_window_days": train_window_days, "test_window_days": test_window_days,
              "window_policy": window_policy, "ridge_alpha": ridge_alpha, "horizons": {}}
    for horizon in horizons:
        result["horizons"][str(horizon)] = _evaluate_horizon(
            rows, int(horizon), train_window_days, test_window_days, window_policy, ridge_alpha)
    return result


def _evaluate_horizon(source, horizon, train_window_days, test_window_days, window_policy, ridge_alpha):
    prefix = "t%d_" % horizon
    frame = sorted((dict(row) for row in source if row.get(prefix + "label_status") == "MATURE"),
                   key=lambda row: (row["trade_date"], row["ticker"]))
    dates = sorted({row["trade_date"] for row in frame})
    if not dates:
        raise ValueError("horizon %d has no mature rows" % horizon)
    availability_column = prefix + "label_available_trade_date"
    availability_by_date = {}
    for row in frame:
        day, available = row["trade_date"], row[availability_column]
        availability_by_date[day] = max(availability_by_date.get(day, available), available)
    first_start = _first_causal_start(dates, availability_by_date, train_window_days)
    if first_start is None:
        raise ValueError("horizon %d lacks %d causally mature dates" % (horizon, train_window_days))
    returns, probabilities, drawdowns, periods, daily_return_errors = [], [], [], [], {}
    for start in range(first_start, len(dates), test_window_days):
        test_dates, cutoff = dates[start:start + test_window_days], dates[start - 1]
        eligible = _eligible_dates(dates[:start], availability_by_date, cutoff)
        train_dates = eligible if window_policy == "expanding" else eligible[-train_window_days:]
        train = [row for row in frame if row["trade_date"] in train_dates]
        return_model = _fit_ridge_target(train, prefix + "excess_return", ridge_alpha)
        risk_model = _fit_ridge_target(train, prefix + "max_close_drawdown", ridge_alpha)
        probability_model = _fit_logistic(train, prefix + "is_up")
        test = [row for row in frame if row["trade_date"] in test_dates]
        for row in test:
            prediction, actual = _predict_target(return_model, row), float(row[prefix + "excess_return"])
            returns.append(RegressionObservation(prediction, actual))
            daily_return_errors.setdefault(row["trade_date"], []).append(prediction - actual)
            drawdowns.append(RiskObservation(max(0.0, _predict_target(risk_model, row)),
                                             float(row[prefix + "max_close_drawdown"])))
            probabilities.append(ProbabilityObservation(_predict_logistic(probability_model, row), bool(row[prefix + "is_up"])))
        periods.append({"train_dates": [str(train_dates[0]), str(train_dates[-1])],
                        "label_availability_cutoff_date": str(cutoff),
                        "test_dates": [str(test_dates[0]), str(test_dates[-1])], "training_rows": len(train),
                        "test_rows": len(test)})
    return {"label_columns": {"excess_return": prefix + "excess_return", "is_up": prefix + "is_up",
                               "max_close_drawdown": prefix + "max_close_drawdown",
                               "label_available_trade_date": prefix + "label_available_trade_date"},
            "model_note": "Separate Ridge return/drawdown regressions and L2-regularized linear logistic baseline.",
            "periods": periods, "return_metrics": dict(regression_metrics(returns), daily_mean_error_hac_95=newey_west_mean_interval(
                [sum(values) / len(values) for _, values in sorted(daily_return_errors.items())])),
            "up_probability_metrics": probability_metrics(probabilities),
            "drawdown_metrics": drawdown_risk_metrics(drawdowns, thresholds=(0.05, 0.10, 0.20))}


def _eligible_dates(dates, availability_by_date, cutoff):
    return [day for day in dates if availability_by_date[day] <= cutoff]


def _first_causal_start(dates, availability_by_date, train_window_days):
    for candidate in range(1, len(dates)):
        if len(_eligible_dates(dates[:candidate], availability_by_date, dates[candidate - 1])) >= train_window_days:
            return candidate
    return None


def _fit_ridge_target(rows, target, alpha):
    copied = [dict(row, target_excess_ret_5d=float(row[target])) for row in rows]
    return fit_ridge(copied, alpha)


def _predict_target(model, row):
    return predict_ridge(model, row)


def _fit_logistic(rows, target, l2=1.0, learning_rate=0.1, iterations=25):
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("install the ML extra before running: python -m pip install -e '.[ml]'") from error
    matrix = np.asarray([[row[name] for name in FEATURE_COLUMNS] for row in rows], dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("logistic model received non-finite PIT features")
    labels = np.asarray([float(row[target]) for row in rows], dtype=float)
    means, scales = matrix.mean(axis=0), matrix.std(axis=0); scales[scales == 0] = 1.0
    # A near-constant feature can yield an enormous z-score from rounding noise.
    # Clip only the model input; raw PIT features and their stored artifact remain intact.
    with np.errstate(over="ignore", invalid="ignore"):
        normalized = (matrix - means) / scales
    normalized = np.nan_to_num(normalized, nan=0.0, posinf=10.0, neginf=-10.0)
    design = np.column_stack((np.ones(len(rows)), np.clip(normalized, -10.0, 10.0)))
    weights = np.zeros(design.shape[1])
    for _ in range(iterations):
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            linear = np.nan_to_num(design @ weights, nan=0.0, posinf=40.0, neginf=-40.0)
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(linear, -40, 40)))
        gradient = design.T @ (probabilities - labels) / len(rows) + l2 * weights / len(rows)
        gradient[0] -= l2 * weights[0] / len(rows)
        weights -= learning_rate * np.clip(np.nan_to_num(gradient, nan=0.0, posinf=10.0, neginf=-10.0), -10.0, 10.0)
        weights = np.clip(weights, -20.0, 20.0)
    return {"means": dict(zip(FEATURE_COLUMNS, means)), "scales": dict(zip(FEATURE_COLUMNS, scales)),
            "coefficients": dict(zip(FEATURE_COLUMNS, weights[1:])), "intercept": float(weights[0])}


def _predict_logistic(model, row):
    value = model["intercept"] + sum(model["coefficients"][name] * ((float(row[name]) - model["means"][name]) / model["scales"][name]) for name in FEATURE_COLUMNS)
    return 1.0 / (1.0 + exp(-max(-40.0, min(40.0, value))))
