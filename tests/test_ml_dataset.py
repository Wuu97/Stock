from datetime import date, timedelta
from decimal import Decimal

import pytest

from quant_core.ml_dataset import BenchmarkIncompleteError, SnapshotMissingError, build_cross_sectional_dataset
from quant_core.models import DayBar


def _bar(day, ticker, close):
    return DayBar(day, ticker, close, close, close, close, 100, close * 100, None, None)


def _inputs(days=26):
    calendar = [date(2024, 1, 1) + timedelta(days=index) for index in range(days)]
    bars = [_bar(day, "000001.SZ", Decimal("10") + Decimal(index) / 10) for index, day in enumerate(calendar)]
    adjusted = {(day, "000001.SZ"): Decimal("10") + Decimal(index) / 10 for index, day in enumerate(calendar)}
    benchmark = {day: Decimal("100") + Decimal(index) for index, day in enumerate(calendar)}
    universe = {day: {"000001.SZ"} for day in calendar[19:-5]}
    return calendar, bars, adjusted, benchmark, universe


def test_dataset_uses_exact_fifth_trading_day_and_excludes_immature_tail():
    calendar, bars, adjusted, benchmark, universe = _inputs()
    rows = build_cross_sectional_dataset(bars, calendar, universe, adjusted, benchmark, calendar[0], calendar[-1])
    assert [row.trade_date for row in rows] == calendar[19:-5]
    assert rows[0].target_excess_ret_5d == pytest.approx((12.4 / 11.9 - 1) - (124 / 119 - 1))


def test_adjusted_label_ignores_a_raw_ex_right_price_gap():
    calendar, bars, adjusted, benchmark, universe = _inputs()
    target_day, future_day = calendar[19], calendar[24]
    bars[-2] = _bar(future_day, "000001.SZ", Decimal("6.20"))  # Raw price halves after a split.
    adjusted[(target_day, "000001.SZ")] = Decimal("11.90")
    adjusted[(future_day, "000001.SZ")] = Decimal("12.40")
    row = build_cross_sectional_dataset(bars, calendar, universe, adjusted, benchmark, calendar[0], calendar[-1])[0]
    assert row.target_excess_ret_5d == pytest.approx((12.4 / 11.9 - 1) - (124 / 119 - 1))


def test_dataset_fails_closed_for_missing_pit_snapshot():
    calendar, bars, adjusted, benchmark, universe = _inputs()
    del universe[calendar[20]]
    with pytest.raises(SnapshotMissingError, match="point-in-time universe snapshot is missing for: 2024-01-21"):
        build_cross_sectional_dataset(bars, calendar, universe, adjusted, benchmark, calendar[0], calendar[-1])


def test_dataset_fails_closed_for_missing_benchmark_observation():
    calendar, bars, adjusted, benchmark, universe = _inputs()
    del benchmark[calendar[24]]
    with pytest.raises(BenchmarkIncompleteError, match="benchmark adjusted close is missing"):
        build_cross_sectional_dataset(bars, calendar, universe, adjusted, benchmark, calendar[0], calendar[-1])


def test_cross_section_features_do_not_depend_on_future_prices():
    calendar, bars, adjusted, benchmark, universe = _inputs(30)
    baseline = build_cross_sectional_dataset(bars, calendar, universe, adjusted, benchmark, calendar[0], calendar[-1])
    changed = dict(adjusted)
    changed[(calendar[-1], "000001.SZ")] = Decimal("999")
    revised = build_cross_sectional_dataset(bars, calendar, universe, changed, benchmark, calendar[0], calendar[-1])
    assert [(row.trade_date, row.momentum_20d, row.momentum_20d_zscore) for row in baseline[:-1]] == [
        (row.trade_date, row.momentum_20d, row.momentum_20d_zscore) for row in revised[:-1]
    ]
