"""Run the frozen P1 multi-horizon, causal OOS diagnostics."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import duckdb

from quant_core.multitarget_walk_forward import walk_forward_multitarget
from quant_core.p1_lineage import validate_prediction_dataset_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-manifest")
    parser.add_argument("--horizons", default="5,10,20"); parser.add_argument("--window-policy", choices=("rolling", "expanding"), default="rolling")
    parser.add_argument("--train-window-days", type=int, default=480); parser.add_argument("--test-window-days", type=int, default=60)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    args = parser.parse_args(); output = Path(args.output); dataset = Path(args.dataset)
    if output.exists(): raise FileExistsError("refusing to overwrite research artifact: %s" % output)
    prediction_output = output.with_suffix(output.suffix + ".predictions.jsonl")
    if prediction_output.exists(): raise FileExistsError("refusing to overwrite prediction artifact: %s" % prediction_output)
    manifest_path = Path(args.dataset_manifest) if args.dataset_manifest else dataset.with_suffix(dataset.suffix + ".manifest.json")
    manifest, dataset_hash = validate_prediction_dataset_manifest(dataset, manifest_path)
    connection = duckdb.connect(":memory:")
    try:
        cursor = connection.execute("SELECT * FROM read_parquet(?) ORDER BY trade_date, ticker", [args.dataset])
        columns = [column[0] for column in cursor.description]; rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally: connection.close()
    report = walk_forward_multitarget(rows, tuple(int(value) for value in args.horizons.split(",")), args.train_window_days, args.test_window_days, args.window_policy, args.ridge_alpha)
    prediction_rows = []
    for horizon in report["horizons"].values():
        prediction_rows.extend(horizon.pop("prediction_records"))
    prediction_output.parent.mkdir(parents=True, exist_ok=True)
    prediction_output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in prediction_rows), encoding="utf-8")
    prediction_hash = sha256(prediction_output.read_bytes()).hexdigest()
    parameters = {"horizons": args.horizons, "window_policy": args.window_policy, "train_window_days": args.train_window_days, "test_window_days": args.test_window_days, "ridge_alpha": args.ridge_alpha}
    report.update({"created_at": datetime.now(timezone.utc).isoformat(), "lineage": {"dataset": str(dataset), "dataset_sha256": dataset_hash, "dataset_manifest": str(manifest_path), "dataset_manifest_sha256": sha256(manifest_path.read_bytes()).hexdigest(), "label_schema_version": manifest["label_schema_version"], "model_family": "ridge_regression_plus_linear_logistic_v1", "parameters": parameters, "parameters_sha256": sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest(), "prediction_artifact": str(prediction_output), "prediction_artifact_sha256": prediction_hash, "prediction_rows": len(prediction_rows)}, "scope_note": "RESEARCH only. Prediction diagnostics are not trade simulation; no fees, limits, T+1, NAV, or account promotion is implied."})
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "horizons": {key: value["return_metrics"]["count"] for key, value in report["horizons"].items()}}, ensure_ascii=False))


if __name__ == "__main__": main()
