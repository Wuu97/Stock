from datetime import date, timedelta
from decimal import Decimal

from quant_core.prediction_labels import (MATURE, UNMATURED, UNTRADEABLE_OUTCOME, PredictionTarget,
                                          build_prediction_label)


def _days(count=21):
    return tuple(date(2025, 1, 1) + timedelta(days=index) for index in range(count))


def _inputs(days):
    stock = [Decimal("100"), Decimal("110"), Decimal("90"), Decimal("120"), Decimal("115")]
    adjusted = {(day, "AAA"): stock[index] for index, day in enumerate(days[:5])}
    benchmark = {day: Decimal("100") + index for index, day in enumerate(days)}
    return adjusted, benchmark


def test_mature_label_has_return_direction_drawdown_and_horizon_availability():
    days = _days()
    adjusted, benchmark = _inputs(days)
    label = build_prediction_label(target=PredictionTarget(4), calendar=days, decision_trade_date=days[0],
                                   ticker="AAA", adjusted_closes=adjusted,
                                   benchmark_adjusted_closes=benchmark)
    assert label.status == MATURE
    assert label.label_available_trade_date == days[4]
    assert label.absolute_return == Decimal("0.15")
    assert label.excess_return == Decimal("0.11")
    assert label.is_up is True
    assert label.max_close_drawdown == Decimal("2") / Decimal("11")


def test_tail_is_unmatured_instead_of_using_future_data():
    days = _days(5)
    adjusted, benchmark = _inputs(days)
    label = build_prediction_label(target=PredictionTarget(5), calendar=days, decision_trade_date=days[0],
                                   ticker="AAA", adjusted_closes=adjusted,
                                   benchmark_adjusted_closes=benchmark)
    assert label.status == UNMATURED
    assert label.label_available_trade_date is None
    assert label.absolute_return is None


def test_missing_path_price_is_an_untradeable_outcome():
    days = _days()
    adjusted, benchmark = _inputs(days)
    del adjusted[(days[2], "AAA")]
    label = build_prediction_label(target=PredictionTarget(4), calendar=days, decision_trade_date=days[0],
                                   ticker="AAA", adjusted_closes=adjusted,
                                   benchmark_adjusted_closes=benchmark)
    assert label.status == UNTRADEABLE_OUTCOME
    assert label.label_available_trade_date is None


def test_up_label_respects_versioned_threshold():
    days = _days()
    adjusted, benchmark = _inputs(days)
    label = build_prediction_label(target=PredictionTarget(4, Decimal("0.20")), calendar=days,
                                   decision_trade_date=days[0], ticker="AAA", adjusted_closes=adjusted,
                                   benchmark_adjusted_closes=benchmark)
    assert label.is_up is False
