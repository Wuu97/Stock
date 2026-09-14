from decimal import Decimal

from quant_core.td_sequential import td_sequential_state
from quant_core.bulldozer import BulldozerConfig, build_bulldozer_features
from quant_core.models import DayBar
from datetime import date, timedelta


def test_td_sequential_counts_completed_up_setup_against_four_bars_ago():
    state = td_sequential_state([Decimal(value) for value in range(1, 14)])
    assert (state.direction, state.count, state.run_length, state.label) == ("UP", 9, 9, "UP_9")


def test_td_sequential_counts_completed_down_setup_and_resets_on_equal_close():
    state = td_sequential_state([Decimal(value) for value in range(14, 1, -1)])
    assert (state.direction, state.count) == ("DOWN", 9)
    reset = td_sequential_state([Decimal("10"), Decimal("9"), Decimal("8"), Decimal("7"), Decimal("10")])
    assert reset.label == "NONE"


def test_bulldozer_uses_shanghai_composite_daily_td_for_every_candidate():
    start = date(2026, 1, 2)
    bars = []
    for index in range(13):
        day = start + timedelta(days=index)
        for ticker, close in (("600000.SH", Decimal("10") + Decimal(index) / 10),
                              ("000001.SH", Decimal("3000") + Decimal(index))):
            bars.append(DayBar(day, ticker, close, close, close, close, 1000, Decimal("10000"),
                               Decimal("9999"), Decimal("1")))
    rows = build_bulldozer_features(bars, start + timedelta(days=12), BulldozerConfig(top_n=1))
    candidate = next(row for row in rows if row.ticker == "600000.SH")
    assert (candidate.market_td_direction, candidate.market_td_count) == ("UP", 9)
