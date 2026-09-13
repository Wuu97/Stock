"""Evaluate the deployable Ridge ML shadow model with rolling out-of-sample windows."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import duckdb

from quant_core.ml_walk_forward import walk_forward_ridge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--window-policy", choices=("rolling", "expanding"), default="rolling")
    parser.add_argument("--train-window-days", type=int, default=480)
    parser.add_argument("--test-window-days", type=int, default=60)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--baseline-volume-multiple", type=float, default=1.5)
    args = parser.parse_args()
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?) ORDER BY trade_date, ticker", [args.dataset])
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()
    report = walk_forward_ridge(rows, args.train_window_days, args.test_window_days, args.window_policy,
                                args.top_n, args.ridge_alpha, args.baseline_volume_multiple)
    report.update({"schema_version": "ml_ridge_walk_forward_v1", "created_at": datetime.now(timezone.utc).isoformat(),
                   "dataset": str(Path(args.dataset)), "dataset_sha256": sha256(Path(args.dataset).read_bytes()).hexdigest(),
                   "scope_note": "Research-only adjusted-close excess returns. This does not simulate fees, limits, T+1, or NAV."})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"periods": len(report["periods"]), "rank_ic_mean": report["rank_ic_mean"],
                      "model_top_excess_return_gross_mean": report["model_top_excess_return_gross_mean"],
                      "baseline_top_excess_return_gross_mean": report["baseline_top_excess_return_gross_mean"],
                      "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
