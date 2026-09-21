from datetime import date, timedelta

from scripts.run_walk_forward_study import causal_training_dates


def test_lightgbm_training_dates_exclude_labels_unavailable_at_cutoff():
    start = date(2025, 1, 1)
    dates = [start + timedelta(days=index) for index in range(6)]
    rows = [{"trade_date": day, "label_available_trade_date": day + timedelta(days=2)} for day in dates]
    # At the fifth decision date, only the first two outcomes are observable.
    assert causal_training_dates(rows, dates, 4, 2, "rolling") == dates[:2]
    assert causal_training_dates(rows, dates, 4, 2, "expanding") == dates[:2]
