from datetime import date, timedelta
from decimal import Decimal

from quant_core.features import build_features
from quant_core.models import DayBar
from quant_core.strategy import BaselineConfig, select_baseline


def _bar(day, ticker, close, volume):
    return DayBar(day, ticker, close, close, close, close, volume, Decimal(volume) * close,
                  close + 1, close - 1)


def test_baseline_uses_only_the_as_of_window_and_ranks_qualified_stocks():
    start = date(2026, 8, 1)
    bars = []
    for index in range(20):
        day = start + timedelta(days=index)
        bars.extend([
            _bar(day, "AAA", Decimal("10") + Decimal(index) / 10, 100 if index < 19 else 200),
            _bar(day, "BBB", Decimal("10") + Decimal(index) / 20, 100 if index < 19 else 200),
        ])
    features = build_features(bars, start + timedelta(days=19), 20)
    picks = select_baseline(features, BaselineConfig("baseline", 1.5, 2))
    assert [pick.ticker for pick in picks] == ["AAA", "BBB"]
    assert picks[0].score > picks[1].score


def test_future_bars_do_not_change_features_for_the_as_of_date():
    target = date(2026, 8, 20)
    bars = [_bar(target - timedelta(days=index), "AAA", Decimal("10"), 100) for index in range(20)]
    before = build_features(bars, target, 20)
    after = build_features(bars + [_bar(target + timedelta(days=1), "AAA", Decimal("100"), 10000)], target, 20)
    assert before == after
