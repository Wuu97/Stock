from datetime import date, timedelta
from decimal import Decimal

from quant_core.models import DayBar
from quant_core.signal_study import run_baseline_signal_study, summarize_signal_study
from quant_core.strategy import BaselineConfig


def _bar(day, close):
    return DayBar(day, "600000.SH", close, close, close, close, 100, close * 100, None, None)


def test_signal_study_uses_future_closes_only_after_each_signal_date():
    start = date(2024, 1, 1)
    bars = [_bar(start + timedelta(days=index), Decimal("10") + index) for index in range(25)]
    observations = run_baseline_signal_study(
        bars, ["600000.SH"], start, start + timedelta(days=19), 20,
        BaselineConfig("baseline", 1.0, 1), horizons=(1, 5),
    )
    assert [(row.horizon_days, row.future_trade_date, round(row.close_return, 10)) for row in observations] == [
        (1, start + timedelta(days=20), Decimal("0.0344827586")),
        (5, start + timedelta(days=24), Decimal("0.1724137931")),
    ]
    summary = summarize_signal_study(observations)
    assert summary["T+1"]["positive_signals"] == 1
    assert summary["T+5"]["signals"] == 1
