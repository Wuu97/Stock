"""Run an out-of-sample LightGBM regression study against the rule baseline."""

import argparse
from datetime import datetime, timezone
import json
from math import sqrt
from pathlib import Path

import duckdb


FEATURE_COLUMNS = (
    "momentum_5d", "momentum_20d", "sma20_deviation", "volume_ratio_20d",
    "momentum_20d_percentile", "momentum_20d_zscore",
)


def _rank(values):
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor
        while end + 1 < len(order) and values[order[end + 1]] == values[order[cursor]]:
            end += 1
        average = (cursor + end + 2) / 2
        for index in order[cursor:end + 1]:
            result[index] = average
        cursor = end + 1
    return result


def _rank_ic(predictions, actuals):
    if len(predictions) < 2:
        return None
    x, y = _rank(predictions), _rank(actuals)
    x_mean, y_mean = sum(x) / len(x), sum(y) / len(y)
    numerator = sum((left - x_mean) * (right - y_mean) for left, right in zip(x, y))
    denominator = sqrt(sum((value - x_mean) ** 2 for value in x) * sum((value - y_mean) ** 2 for value in y))
    return numerator / denominator if denominator else None


def _load_rows(dataset_path: str):
    connection = duckdb.connect(":memory:", read_only=False)
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?) ORDER BY trade_date, ticker", [dataset_path])
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        connection.close()
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--window-policy", choices=("rolling", "expanding"), default="rolling")
    parser.add_argument("--train-window-days", type=int, default=480)
    parser.add_argument("--test-window-days", type=int, default=60)
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()
    if args.train_window_days < 1 or args.test_window_days < 1 or args.top_n < 1:
        raise ValueError("study windows and top-n must be positive")
    try:
        from lightgbm import LGBMRegressor
    except ImportError as exc:
        raise RuntimeError("install the ML extra before running: python -m pip install -e '.[ml]'") from exc

    frame = _load_rows(args.dataset)
    dates = sorted({row["trade_date"] for row in frame})
    periods, daily = [], []
    for start in range(args.train_window_days, len(dates), args.test_window_days):
        train_start = 0 if args.window_policy == "expanding" else start - args.train_window_days
        test_dates = dates[start:start + args.test_window_days]
        if not test_dates:
            break
        train_dates = dates[train_start:start]
        train = [row for row in frame if row["trade_date"] in train_dates]
        test = [dict(row) for row in frame if row["trade_date"] in test_dates]
        model = LGBMRegressor(objective="regression", n_estimators=200, learning_rate=0.03,
                              num_leaves=15, min_child_samples=30, random_state=7, verbosity=-1)
        model.fit([[row[column] for column in FEATURE_COLUMNS] for row in train],
                  [row["target_excess_ret_5d"] for row in train])
        predictions = model.predict([[row[column] for column in FEATURE_COLUMNS] for row in test])
        for row, prediction in zip(test, predictions):
            row["prediction"] = float(prediction)
        period_ics = []
        for trade_date in test_dates:
            cross_section = [row for row in test if row["trade_date"] == trade_date]
            ic = _rank_ic([row["prediction"] for row in cross_section], [row["target_excess_ret_5d"] for row in cross_section])
            top_model = sorted(cross_section, key=lambda row: (-row["prediction"], row["ticker"]))[:args.top_n]
            qualified = [row for row in cross_section if row["sma20_deviation"] > 0 and row["volume_ratio_20d"] >= 1.0]
            top_rule = sorted(qualified, key=lambda row: (-row["momentum_20d"], row["ticker"]))[:args.top_n]
            daily.append({"trade_date": str(trade_date), "rank_ic": ic,
                          "model_top_excess_return_gross": sum(row["target_excess_ret_5d"] for row in top_model) / len(top_model),
                          "baseline_top_excess_return_gross": sum(row["target_excess_ret_5d"] for row in top_rule) / len(top_rule) if top_rule else None,
                          "model_picks": [row["ticker"] for row in top_model], "baseline_picks": [row["ticker"] for row in top_rule]})
            if ic is not None:
                period_ics.append(ic)
        periods.append({"train_dates": [str(train_dates[0]), str(train_dates[-1])],
                        "test_dates": [str(test_dates[0]), str(test_dates[-1])], "training_rows": len(train),
                        "rank_ic_mean": sum(period_ics) / len(period_ics) if period_ics else None,
                        "model_params": model.get_params()})
    if not periods:
        raise ValueError("dataset does not contain enough dates for one walk-forward period")
    valid_ics = [row["rank_ic"] for row in daily if row["rank_ic"] is not None]
    report = {
        "schema_version": "walk_forward_study_v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(Path(args.dataset)), "window_policy": args.window_policy,
        "feature_columns": list(FEATURE_COLUMNS), "periods": periods, "daily_oos": daily,
        "rank_ic_mean": sum(valid_ics) / len(valid_ics) if valid_ics else None,
        "scope_note": "Research-only gross adjusted-close excess returns. This report does not simulate execution costs or NAV.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"periods": len(periods), "rank_ic_mean": report["rank_ic_mean"], "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
