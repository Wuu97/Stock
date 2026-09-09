"""Build baseline features from one local snapshot and freeze a recommendation run."""

import argparse
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from quant_core.features import build_features
from quant_core.event_shadow import active_event_adjustments, augment_recommendations
from quant_core.market_data import MarketDataStore
from quant_core.recommendations import store_recommendations
from quant_core.snapshots import SnapshotService
from quant_core.strategy import BaselineConfig, rank_baseline
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--effective-as-of", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--artifact-dir", default="data/snapshots")
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--volume-multiple", type=float, default=1.5)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--universe-snapshot-id", default=None)
    parser.add_argument("--event-shadow", action="store_true")
    parser.add_argument("--taxonomy-version")
    parser.add_argument("--event-weight", type=Decimal, default=Decimal("0.20"))
    args = parser.parse_args()

    as_of_date = date.fromisoformat(args.as_of_date)
    target_date = date.fromisoformat(args.target_date)
    effective_as_of = datetime.fromisoformat(args.effective_as_of)
    if effective_as_of.tzinfo is None:
        raise ValueError("--effective-as-of must include a timezone")
    if args.event_shadow and not args.taxonomy_version:
        raise ValueError("--taxonomy-version is required with --event-shadow")
    connection = duckdb.connect(args.db)
    data_store = MarketDataStore(connection)
    features = build_features(data_store.load_bars_many(args.market_snapshot_id), as_of_date, args.lookback_days)
    if args.universe_snapshot_id:
        allowed = UniverseService(connection).member_tickers(args.universe_snapshot_id)
        features = [row for row in features if row.ticker in allowed]
    feature_id = str(uuid4())
    artifact_path = Path(args.artifact_dir) / f"features_{feature_id}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_payload = [row.__dict__ | {"as_of_trade_date": row.as_of_trade_date.isoformat()} for row in features]
    artifact_path.write_text(json.dumps(artifact_payload, default=str, sort_keys=True), encoding="utf-8")
    artifact_hash = sha256(artifact_path.read_bytes()).hexdigest()
    now = datetime.now(effective_as_of.tzinfo)
    snapshots = SnapshotService(connection)
    snapshots.register_feature_snapshot(feature_id, as_of_date, args.lookback_days, str(artifact_path),
                                        artifact_hash, args.market_snapshot_id, now)
    run_id = str(uuid4())
    status = snapshots.freeze_run(run_id, target_date, "momentum_trend_v1", "baseline_v1", "cost_a_share_2026_v1",
                                  feature_id, effective_as_of, now)
    candidates = rank_baseline(features, BaselineConfig("momentum_trend_v1", args.volume_multiple, args.top_n))
    if status == "FROZEN":
        store_recommendations(connection, run_id, candidates[:args.top_n], now)
    shadow_run_id, shadow_status = None, None
    if args.event_shadow:
        adjustments = active_event_adjustments(connection, args.taxonomy_version, as_of_date, effective_as_of)
        shadow_recommendations = augment_recommendations(candidates, adjustments, args.event_weight, args.top_n)
        shadow_run_id = str(uuid4())
        shadow_status = snapshots.freeze_run(shadow_run_id, target_date, "momentum_trend_event_shadow_v1",
                                             "event_shadow_v1", "cost_a_share_2026_v1", feature_id,
                                             effective_as_of, now)
        if shadow_status == "FROZEN":
            connection.execute("INSERT INTO recommendation_run_modes VALUES (?, 'SHADOW', ?)", [shadow_run_id, now])
            store_recommendations(connection, shadow_run_id, shadow_recommendations, now)
    connection.close()
    print(json.dumps({"baseline_run_id": run_id, "baseline_status": status, "shadow_run_id": shadow_run_id,
                      "shadow_status": shadow_status, "event_shadow_enabled": args.event_shadow}))


if __name__ == "__main__":
    main()
