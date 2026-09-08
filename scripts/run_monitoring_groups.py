"""Freeze recommendations for every configured monitoring group after the market close."""

import argparse
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from quant_core.features import build_features
from quant_core.market_data import MarketDataStore
from quant_core.recommendations import store_recommendations
from quant_core.snapshots import SnapshotService
from quant_core.strategy import BaselineConfig, select_baseline
from quant_core.universe import DynamicUniverseRule, UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--market-cap-snapshot-id", required=True)
    parser.add_argument("--groups-config", default="config/monitoring_groups.json")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--effective-as-of", required=True)
    parser.add_argument("--artifact-dir", default="data/monitoring_runs")
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--volume-multiple", type=float, default=1.5)
    parser.add_argument("--recommendation-top-n", type=int, default=5)
    args = parser.parse_args()

    effective_as_of = datetime.fromisoformat(args.effective_as_of)
    if effective_as_of.tzinfo is None:
        raise ValueError("--effective-as-of must include a timezone")
    as_of_date, target_date = date.fromisoformat(args.as_of_date), date.fromisoformat(args.target_date)
    groups = json.loads(Path(args.groups_config).read_text(encoding="utf-8")).get("groups", [])
    if not groups:
        raise ValueError("monitoring config has no groups")
    connection = duckdb.connect(args.db)
    snapshots, universes = SnapshotService(connection), UniverseService(connection)
    bars = MarketDataStore(connection).load_bars_many(args.market_snapshot_id)
    all_features = build_features(bars, as_of_date, args.lookback_days)
    now, results = datetime.now(effective_as_of.tzinfo), []
    for group in groups:
        rule = DynamicUniverseRule(group["name"], Decimal(str(group["min_total_market_cap"])),
                                   int(group["momentum_window_days"]), int(group["top_n"]))
        universe_id = universes.create_snapshot(args.market_cap_snapshot_id, as_of_date, rule, bars, now)
        allowed = universes.member_tickers(universe_id)
        features = [row for row in all_features if row.ticker in allowed]
        feature_id = str(uuid4())
        artifact_path = Path(args.artifact_dir) / f"features_{group['name']}_{feature_id}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps([row.__dict__ | {"as_of_trade_date": row.as_of_trade_date.isoformat()} for row in features], default=str, sort_keys=True), encoding="utf-8")
        snapshots.register_feature_snapshot(feature_id, as_of_date, args.lookback_days, str(artifact_path),
                                            sha256(artifact_path.read_bytes()).hexdigest(), args.market_snapshot_id, now)
        run_id = str(uuid4())
        status = snapshots.freeze_run(run_id, target_date, f"momentum_trend_{group['name']}", "baseline_v1",
                                      "cost_a_share_2026_v1", feature_id, effective_as_of, now)
        if status == "FROZEN":
            store_recommendations(connection, run_id, select_baseline(features, BaselineConfig(
                f"momentum_trend_{group['name']}", args.volume_multiple, args.recommendation_top_n
            )), now)
        results.append({"group": group["name"], "universe_snapshot_id": universe_id, "run_id": run_id, "status": status})
    connection.close()
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
