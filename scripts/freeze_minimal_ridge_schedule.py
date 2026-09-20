"""Freeze the first causally eligible 480/60 Ridge schedule from an immutable dataset."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import duckdb

from quant_core.ml_shadow import FEATURE_COLUMNS, MODEL_SCHEMA_VERSION
from quant_core.ml_walk_forward import fit_ridge
from quant_core.ridge_schedule import SCHEDULE_SCHEMA_VERSION


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--profile-version", required=True)
    parser.add_argument("--profile-hash", required=True)
    parser.add_argument("--model-output", required=True)
    parser.add_argument("--schedule-output", required=True)
    parser.add_argument("--train-window-days", type=int, default=480)
    parser.add_argument("--test-window-days", type=int, default=60)
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--prediction-start-date", help="Require this verified first prediction date instead of choosing the earliest.")
    parser.add_argument("--prediction-end-date", help="Optional execution end beyond mature label dates for the final OOS segment.")
    args = parser.parse_args()
    if args.train_window_days < 1 or args.test_window_days < 1 or args.ridge_alpha < 0:
        raise ValueError("invalid Ridge schedule parameters")
    dataset, manifest = Path(args.dataset), Path(args.dataset_manifest)
    model_path, schedule_path = Path(args.model_output), Path(args.schedule_output)
    if not dataset.is_file() or not manifest.is_file():
        raise ValueError("dataset and its manifest must both exist")
    if model_path.exists() or schedule_path.exists():
        raise FileExistsError("frozen Ridge model or schedule already exists")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    if manifest_payload.get("dataset_sha256") != _sha(dataset):
        raise ValueError("dataset hash does not match immutable manifest")
    if manifest_payload.get("label_availability_contract") != "label_available_trade_date_is_t_plus_5_trading_day":
        raise ValueError("dataset does not declare the T+5 label availability contract")
    con = duckdb.connect(":memory:")
    try:
        cursor = con.execute("SELECT * FROM read_parquet(?) ORDER BY trade_date,ticker", [str(dataset)])
        cols = [item[0] for item in cursor.description]
        if "label_available_trade_date" not in cols:
            raise ValueError("dataset predates the label availability contract")
        rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    finally:
        con.close()
    mature = [row for row in rows if row["label_status"] == "MATURE"]
    dates = sorted({row["trade_date"] for row in mature})
    by_day = {day: [row for row in mature if row["trade_date"] == day] for day in dates}
    if any(row.get("label_available_trade_date") is None for row in mature):
        raise ValueError("mature label row has no availability date")
    start = next((index for index in range(1, len(dates) - args.test_window_days + 1)
                  if len([day for day in dates[:index] if all(row["label_available_trade_date"] <= dates[index - 1] for row in by_day[day])]) >= args.train_window_days), None)
    if start is None:
        raise ValueError("no causally eligible 480-day training and 60-day prediction window")
    if args.prediction_start_date:
        requested = date.fromisoformat(args.prediction_start_date)
        if dates[start] != requested:
            matching = [index for index in range(1, len(dates))
                        if dates[index] == requested]
            if not matching:
                raise ValueError("requested prediction start is absent from the frozen dataset")
            start = matching[0]
            cutoff = dates[start - 1]
            eligible = [day for day in dates[:start] if all(row["label_available_trade_date"] <= cutoff for row in by_day[day])]
            if len(eligible) < args.train_window_days:
                raise ValueError("requested prediction start lacks a causally mature 480-day training window")
    cutoff = dates[start - 1]
    eligible = [day for day in dates[:start] if all(row["label_available_trade_date"] <= cutoff for row in by_day[day])]
    train_dates = eligible[-args.train_window_days:]
    test_dates = dates[start:start + args.test_window_days]
    prediction_end = date.fromisoformat(args.prediction_end_date) if args.prediction_end_date else test_dates[-1]
    train = [row for day in train_dates for row in by_day[day]]
    if len(train_dates) != args.train_window_days or (len(test_dates) != args.test_window_days and not args.prediction_start_date):
        raise ValueError("frozen Ridge schedule is not a complete 480/60 window")
    fitted = fit_ridge(train, args.ridge_alpha)
    payload = {
        "schema_version": MODEL_SCHEMA_VERSION, "model_type": "ridge_excess_return_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trained_through_date": cutoff.isoformat(), "label_availability_cutoff_date": cutoff.isoformat(),
        "training_decision_dates": [train_dates[0].isoformat(), train_dates[-1].isoformat()],
        "training_rows": len(train), "ridge_alpha": args.ridge_alpha,
        "feature_columns": list(FEATURE_COLUMNS), "feature_means": fitted["means"],
        "feature_scales": fitted["scales"], "coefficients": fitted["coefficients"],
        "intercept": fitted["intercept"], "source_dataset": str(dataset),
        "source_dataset_sha256": _sha(dataset), "source_dataset_manifest_sha256": _sha(manifest),
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    schedule = {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "dataset_sha256": _sha(dataset), "dataset_manifest_sha256": _sha(manifest),
        "competition_profile": {"id": args.profile_id, "version": args.profile_version, "hash": args.profile_hash},
        "models": [{"model_path": str(model_path), "model_sha256": _sha(model_path),
                    "training_decision_dates": payload["training_decision_dates"],
                    "label_availability_cutoff_date": cutoff.isoformat(),
                    "prediction_dates": [test_dates[0].isoformat(), prediction_end.isoformat()]}],
    }
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(json.dumps(schedule, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"training_dates": payload["training_decision_dates"], "test_dates": schedule["models"][0]["prediction_dates"],
                      "training_rows": len(train), "model_sha256": _sha(model_path), "schedule_sha256": _sha(schedule_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
