import pytest

from quant_core.prediction_evaluation import (ProbabilityObservation, RegressionObservation, RiskObservation,
                                              drawdown_risk_metrics, newey_west_mean_interval, probability_metrics,
                                              regression_metrics)


def test_regression_metrics_report_error_and_rank_information():
    metrics = regression_metrics((RegressionObservation(.1, .2), RegressionObservation(.3, .4)))
    assert metrics["count"] == 2
    assert metrics["mae"] == pytest.approx(.1)
    assert metrics["rmse"] == pytest.approx(.1)
    assert metrics["rank_ic"] == pytest.approx(1)


def test_probability_metrics_are_bounded_and_calibrated_by_bucket():
    metrics = probability_metrics((ProbabilityObservation(.1, False), ProbabilityObservation(.9, True)), bins=2)
    assert metrics["brier"] == pytest.approx(.01)
    assert metrics["log_loss"] == pytest.approx(-__import__("math").log(.9))
    assert [row["observed_frequency"] for row in metrics["calibration"]] == [0, 1]
    with pytest.raises(ValueError, match="between zero and one"):
        probability_metrics((ProbabilityObservation(1.1, True),))


def test_drawdown_metrics_report_threshold_precision_and_recall():
    metrics = drawdown_risk_metrics((RiskObservation(.1, .12), RiskObservation(.02, .15)), (.1,))
    row = metrics["thresholds"][0]
    assert metrics["mae"] == pytest.approx(.075)
    assert row["precision"] == 1
    assert row["recall"] == .5


def test_newey_west_interval_uses_available_daily_observations():
    result = newey_west_mean_interval([1.0, 2.0, 3.0, 4.0], lags=10)
    assert result["count"] == 4
    assert result["lags"] == 3
    assert result["lower_95"] < result["mean"] < result["upper_95"]
