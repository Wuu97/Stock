"""Run the frozen P1 multi-horizon, causal OOS diagnostics."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import duckdb

from quant_core.multitarget_walk_forward import walk_forward_multitarget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--horizons", default="5,10,20"); parser.add_argument("--window-policy", choices=("rolling", "expanding"), default="rolling")
    parser.add_argument("--train-window-days", type=int, default=480); parser.add_argument("--test-window-days", type=int, default=60)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    args = parser.parse_args(); output = Path(args.output)
    if output.exists(): raise FileExistsError("refusing to overwrite research artifact: %s" % output)
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?) ORDER BY trade_date, ticker", [args.dataset])
        columns = [column[0] for column in cursor.description]; rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally: connection.close()
    report = walk_forward_multitarget(rows, tuple(int(value) for value in args.horizons.split(",")), args.train_window_days, args.test_window_days, args.window_policy, args.ridge_alpha)
    report.update({"created_at": datetime.now(timezone.utc).isoformat(), "dataset": str(Path(args.dataset)), "dataset_sha256": sha256(Path(args.dataset).read_bytes()).hexdigest(), "scope_note": "RESEARCH only. Prediction diagnostics are not trade simulation; no fees, limits, T+1, NAV, or account promotion is implied."})
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "horizons": {key: value["return_metrics"]["count"] for key, value in report["horizons"].items()}}, ensure_ascii=False))


if __name__ == "__main__": main()
