"""Evaluate paired production and shadow runs under the same daily execution model."""

import argparse
from decimal import Decimal
import json

import duckdb

from quant_core.market_data import MarketDataStore
from quant_core.models import FeeModel
from quant_core.track_evaluation import evaluate_tracks


def _paired_run_ids(connection) -> list[str]:
    """Return frozen baseline/shadow pairs sharing one decision snapshot and cutoff."""
    rows = connection.execute(
        "SELECT DISTINCT baseline.run_id, shadow.run_id "
        "FROM recommendation_runs baseline "
        "LEFT JOIN recommendation_run_modes baseline_mode ON baseline_mode.run_id = baseline.run_id "
        "JOIN recommendation_runs shadow ON shadow.target_trade_date = baseline.target_trade_date "
        "AND shadow.feature_snapshot_id = baseline.feature_snapshot_id "
        "AND shadow.effective_as_of_timestamp = baseline.effective_as_of_timestamp "
        "JOIN recommendation_run_modes shadow_mode ON shadow_mode.run_id = shadow.run_id "
        "WHERE baseline.run_status = 'FROZEN' AND shadow.run_status = 'FROZEN' "
        "AND COALESCE(baseline_mode.execution_mode, 'PRODUCTION') = 'PRODUCTION' "
        "AND shadow_mode.execution_mode = 'SHADOW'"
    ).fetchall()
    return [run_id for pair in rows for run_id in pair]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-id", action="append")
    parser.add_argument("--all-paired", action="store_true", help="Evaluate every frozen baseline/shadow run pair.")
    parser.add_argument("--market-snapshot-id", action="append", required=True)
    parser.add_argument("--benchmark-ticker", required=True)
    parser.add_argument("--evaluation-version", default="track_evaluation_v1")
    parser.add_argument("--shares", type=int, default=100)
    parser.add_argument("--commission-rate", default="0.00025")
    parser.add_argument("--min-commission", default="5")
    parser.add_argument("--stamp-duty-rate", default="0.0005")
    parser.add_argument("--transfer-fee-rate", default="0.00001")
    parser.add_argument("--slippage-rate", default="0.001")
    args = parser.parse_args()
    if bool(args.run_id) == args.all_paired:
        parser.error("provide --run-id at least once, or use --all-paired")
    fee = FeeModel("cost_a_share_2026_v1", Decimal(args.commission_rate), Decimal(args.min_commission),
                   Decimal(args.stamp_duty_rate), Decimal(args.transfer_fee_rate), Decimal(args.slippage_rate))
    connection = duckdb.connect(args.db)
    try:
        run_ids = args.run_id or _paired_run_ids(connection)
        if not run_ids:
            raise ValueError("no frozen baseline/shadow recommendation pairs were found")
        summary = evaluate_tracks(connection, run_ids, MarketDataStore(connection).load_bars_many(args.market_snapshot_id),
                                  args.benchmark_ticker, fee, args.evaluation_version, args.shares)
    finally:
        connection.close()
    print(json.dumps(summary, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
