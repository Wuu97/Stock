from datetime import date, timedelta

from quant_core.ml_shadow import FEATURE_COLUMNS
from quant_core.multitarget_walk_forward import walk_forward_multitarget


def test_multitarget_walk_forward_is_causal_and_reports_all_diagnostics():
    start, rows = date(2024, 1, 1), []
    for offset in range(9):
        for ticker, signal in (("AAA", 1.0), ("BBB", -1.0)):
            row = {"trade_date": start + timedelta(days=offset), "ticker": ticker}
            row.update({name: signal if name == "momentum_20d" else 0.0 for name in FEATURE_COLUMNS})
            for horizon in (5, 10):
                row.update({"t%d_label_status" % horizon: "MATURE", "t%d_label_available_trade_date" % horizon: start + timedelta(days=offset + 1), "t%d_excess_return" % horizon: signal, "t%d_is_up" % horizon: signal > 0, "t%d_max_close_drawdown" % horizon: 0.1 if signal < 0 else 0.02})
            rows.append(row)
    report = walk_forward_multitarget(rows, horizons=(5, 10), train_window_days=3, test_window_days=2)
    assert set(report["horizons"]) == {"5", "10"}
    result = report["horizons"]["5"]
    assert result["periods"][0]["train_dates"] == ["2024-01-01", "2024-01-03"]
    assert result["periods"][0]["label_availability_cutoff_date"] == "2024-01-04"
    assert result["return_metrics"]["count"] == result["up_probability_metrics"]["count"] == result["drawdown_metrics"]["count"]
    assert 0 <= result["up_probability_metrics"]["brier"] <= 1
