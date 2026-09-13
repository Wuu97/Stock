"""Train one auditable ridge-regression model for ML shadow inference."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

import duckdb

from quant_core.ml_shadow import FEATURE_COLUMNS, MODEL_SCHEMA_VERSION
from quant_core.database import writer_connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, help="PIT-safe Parquet output from build_dataset.py")
    parser.add_argument("--train-end-date", required=True, help="Last labelled decision date allowed in training")
    parser.add_argument("--output", required=True)
    parser.add_argument("--db", help="Optional DuckDB registry for the trained model artifact")
    parser.add_argument("--ridge-alpha", type=float, default=1.0)
    parser.add_argument("--min-training-rows", type=int, default=500)
    args = parser.parse_args()
    if args.ridge_alpha < 0 or args.min_training_rows < 1:
        raise ValueError("ridge alpha must be non-negative and minimum rows must be positive")
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("install the ML extra before training: python -m pip install -e '.[ml]'") from error
    train_end = date.fromisoformat(args.train_end_date)
    connection = duckdb.connect(":memory:")
    try:
        query = "SELECT * FROM read_parquet(?) WHERE label_status = 'MATURE' AND trade_date <= ? ORDER BY trade_date, ticker"
        cursor = connection.execute(query, [args.dataset, train_end])
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        connection.close()
    if len(rows) < args.min_training_rows:
        raise ValueError("not enough mature PIT rows for ML training")
    matrix = np.asarray([[row[name] for name in FEATURE_COLUMNS] for row in rows], dtype=float)
    labels = np.asarray([row["target_excess_ret_5d"] for row in rows], dtype=float)
    means = matrix.mean(axis=0)
    scales = matrix.std(axis=0)
    scales[scales == 0] = 1.0
    normalized = (matrix - means) / scales
    design = np.column_stack((np.ones(len(normalized)), normalized))
    penalty = np.eye(design.shape[1]) * args.ridge_alpha
    penalty[0, 0] = 0.0
    weights = np.linalg.solve(design.T @ design + penalty, design.T @ labels)
    dataset_path = Path(args.dataset)
    payload = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "model_type": "ridge_excess_return_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "trained_through_date": train_end.isoformat(),
        "training_rows": len(rows),
        "ridge_alpha": args.ridge_alpha,
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_means": dict(zip(FEATURE_COLUMNS, [float(value) for value in means])),
        "feature_scales": dict(zip(FEATURE_COLUMNS, [float(value) for value in scales])),
        "coefficients": dict(zip(FEATURE_COLUMNS, [float(value) for value in weights[1:]])),
        "intercept": float(weights[0]),
        "source_dataset": str(dataset_path),
        "source_dataset_sha256": sha256(dataset_path.read_bytes()).hexdigest(),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    model_hash = sha256(output.read_bytes()).hexdigest()
    if args.db:
        with writer_connection(args.db) as connection:
            connection.execute("INSERT OR REPLACE INTO ml_model_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                model_hash, payload["model_type"], str(output), train_end, len(rows), payload["source_dataset_sha256"],
                json.dumps({"ridge_alpha": args.ridge_alpha, "feature_columns": list(FEATURE_COLUMNS)}, sort_keys=True),
                datetime.now(timezone.utc),
            ])
    print(json.dumps({"training_rows": len(rows), "trained_through_date": train_end.isoformat(),
                      "model_sha256": model_hash, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
