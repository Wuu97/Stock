from datetime import date, timedelta

from quant_core.ml_shadow import FEATURE_COLUMNS
from quant_core.ml_walk_forward import walk_forward_ridge


def test_ridge_walk_forward_trains_before_each_test_window():
    start = date(2024, 1, 1)
    rows = []
    for offset in range(8):
        for ticker, signal in (("AAA", 1.0), ("BBB", -1.0), ("CCC", 0.5)):
            row = {"trade_date": start + timedelta(days=offset), "ticker": ticker, "label_status": "MATURE",
                   "label_available_trade_date": start + timedelta(days=offset),
                   "target_excess_ret_5d": signal, "sma20_deviation": 0.01, "volume_ratio_20d": 2.0}
            row.update({name: signal if name == "momentum_20d" else 0.0 for name in FEATURE_COLUMNS})
            rows.append(row)
    report = walk_forward_ridge(rows, train_window_days=3, test_window_days=2, top_n=1)
    assert report["periods"][0]["train_dates"] == ["2024-01-01", "2024-01-03"]
    assert report["periods"][0]["test_dates"] == ["2024-01-04", "2024-01-05"]
    assert report["daily_oos"][0]["model_picks"] == ["AAA"]
    assert report["prediction_metrics"]["count"] == len(report["daily_oos"]) * 3
    assert report["prediction_metrics"]["pooled_rank_correlation"] is not None


def test_ridge_walk_forward_excludes_labels_not_available_at_training_cutoff():
    start = date(2024, 1, 1)
    rows = []
    for offset in range(6):
        row = {"trade_date": start + timedelta(days=offset), "ticker": "AAA", "label_status": "MATURE",
               "label_available_trade_date": start + timedelta(days=offset + 2), "target_excess_ret_5d": 1.0,
               "sma20_deviation": 0.01, "volume_ratio_20d": 2.0}
        row.update({name: 1.0 if name == "momentum_20d" else 0.0 for name in FEATURE_COLUMNS})
        rows.append(row)
    report = walk_forward_ridge(rows, train_window_days=3, test_window_days=1, top_n=1)
    assert report["periods"][0]["label_availability_cutoff_date"] == "2024-01-05"
    # Exactly three labels are observable by the first permissible cutoff.
    assert report["periods"][0]["training_rows"] == 3
