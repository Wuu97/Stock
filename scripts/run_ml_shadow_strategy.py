"""Freeze ML-ranked recommendations in SHADOW mode; this script never creates orders."""

import argparse
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from quant_core.database import writer_connection

from quant_core.market_data import MarketDataStore
from quant_core.ml_shadow import MLShadowModel, build_ml_shadow_features
from quant_core.recommendations import store_recommendations
from quant_core.snapshots import SnapshotService
from quant_core.strategy import Recommendation
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--effective-as-of", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--model", required=True)
    parser.add_argument("--artifact-dir", default="data/snapshots")
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--universe-snapshot-id")
    args = parser.parse_args()
    as_of_date, target_date = date.fromisoformat(args.as_of_date), date.fromisoformat(args.target_date)
    effective_as_of = datetime.fromisoformat(args.effective_as_of)
    if effective_as_of.tzinfo is None:
        raise ValueError("--effective-as-of must include a timezone")
    if args.top_n < 1:
        raise ValueError("--top-n must be positive")
    model_path = Path(args.model)
    model = MLShadowModel.load(model_path)
    if model.trained_through_date >= as_of_date:
        raise ValueError("ML shadow model must be trained strictly before its decision date")
    connection = writer_connection(args.db, transaction=False)
    try:
        bars = MarketDataStore(connection).load_bars_many(args.market_snapshot_id)
        features = build_ml_shadow_features(bars, as_of_date, args.lookback_days)
        if args.universe_snapshot_id:
            allowed = UniverseService(connection).member_tickers(args.universe_snapshot_id)
            features = tuple(row for row in features if row.ticker in allowed)
        ranked = sorted(((row, model.predict(row.values)) for row in features),
                        key=lambda item: (-item[1], item[0].ticker))[:args.top_n]
        feature_id = str(uuid4())
        artifact_path = Path(args.artifact_dir) / f"ml_features_{feature_id}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [{"ticker": row.ticker, "close": row.close, "features": dict(row.values),
                    "predicted_excess_return_5d": score, "model_sha256": model.artifact_sha256}
                   for row, score in ranked]
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        now = datetime.now(effective_as_of.tzinfo)
        snapshots = SnapshotService(connection)
        snapshots.register_feature_snapshot(feature_id, as_of_date, args.lookback_days, str(artifact_path),
                                            sha256(artifact_path.read_bytes()).hexdigest(), args.market_snapshot_id, now)
        run_id = str(uuid4())
        status = snapshots.freeze_run(run_id, target_date, "ml_excess_return_shadow_v1", "ridge_v1",
                                      "cost_a_share_2026_v1", feature_id, effective_as_of, now)
        if status == "FROZEN":
            connection.execute("INSERT INTO recommendation_run_modes VALUES (?, 'SHADOW', ?)", [run_id, now])
            recommendations = [Recommendation(row.ticker, index, score, row.close, {
                "provider": "ML_SHADOW_RIDGE", "predicted_excess_return_5d": str(score),
                "model_sha256": model.artifact_sha256, "trained_through_date": model.trained_through_date.isoformat(),
            }) for index, (row, score) in enumerate(ranked, start=1)]
            store_recommendations(connection, run_id, recommendations, now)
    finally:
        connection.close()
    print(json.dumps({"run_id": run_id, "status": status, "recommendations": len(ranked),
                      "model_sha256": model.artifact_sha256}, ensure_ascii=False))


if __name__ == "__main__":
    main()
