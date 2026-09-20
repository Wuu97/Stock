"""Run one non-comparable, strict V4 Ridge validation window from frozen artifacts."""

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.competition_engine_binding import validate_engine_binding
from quant_core.competition_profile_binding import market_binding
from quant_core.database import writer_connection
from quant_core.derived_evaluation import load_frozen_profile, validate_benchmark_dates
from quant_core.experiments import BenchmarkClose, calculate_metrics
from quant_core.market_data import MarketDataStore
from quant_core.matching import OpenGapPolicy
from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy
from quant_core.ridge_schedule import RidgeModelSchedule, ScheduledMLRidgeScoreProvider
from quant_core.risk import ExitRule
from quant_core.settlement import SettlementService
from quant_core.strategy_research import ML_RIDGE_PROVIDER_TYPE, StrategySpec
from quant_core.trading_status import TradingStatusStore
from quant_core.universe import UniverseService


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _benchmark(path: Path, start: date, end: date):
    with path.open(encoding="utf-8", newline="") as source:
        return [BenchmarkClose(date.fromisoformat(row["trade_date"]), Decimal(row["adj_close"]))
                for row in csv.DictReader(source) if start <= date.fromisoformat(row["trade_date"]) <= end]


def _sell_only(value: str) -> Decimal:
    if not value.endswith("_sell_only"):
        raise ValueError("invalid frozen sell-only stamp duty")
    return Decimal(value.removesuffix("_sell_only"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--profile-version", required=True)
    parser.add_argument("--schedule", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--benchmark-csv", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output, dataset, manifest, schedule_path = map(Path, (args.output, args.dataset, args.dataset_manifest, args.schedule))
    if output.exists():
        raise FileExistsError("validation report already exists")
    schedule = RidgeModelSchedule.load(schedule_path)
    raw_schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    if raw_schedule.get("dataset_sha256") != _sha(dataset) or raw_schedule.get("dataset_manifest_sha256") != _sha(manifest):
        raise ValueError("Ridge schedule input hashes do not match the frozen dataset")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    if manifest_payload.get("dataset_sha256") != _sha(dataset):
        raise ValueError("dataset manifest hash mismatch")
    if len(schedule.entries) != 1:
        raise ValueError("minimal validation must use exactly one frozen 60-day model schedule entry")
    entry = schedule.entries[0]
    start, end = entry.prediction_start, entry.prediction_end
    connection = writer_connection(args.db, transaction=False)
    try:
        if connection.execute("SELECT 1 FROM sim_accounts WHERE account_id=?", [args.account_id]).fetchone():
            raise ValueError("Ridge validation account must be new")
        profile, profile_hash = load_frozen_profile(connection, args.profile_id, args.profile_version)
        expected_profile = {"id": args.profile_id, "version": args.profile_version, "hash": profile_hash}
        if raw_schedule.get("competition_profile") != expected_profile:
            raise ValueError("Ridge schedule is not bound to this frozen V4 profile")
        profile_start, profile_end = map(date.fromisoformat, profile["market_data_binding"]["date_range"])
        validate_engine_binding(connection, profile["engine_binding"], profile_start, profile_end)
        market = profile["market_data_binding"]
        if market_binding(connection, market["source"], profile_start, profile_end) != market:
            raise ValueError("frozen V4 market binding evidence mismatch")
        replay, engine = profile["replay_assumptions"], profile["engine_binding"]
        group = profile["universe_binding"]["group_id"]
        universe = UniverseService(connection).members_by_trade_date(group, start, end)
        if set(universe) != {entry.prediction_start + timedelta(days=0)} and len(universe) != 60:
            # Trading days are not calendar days; cardinality is the strict condition.
            raise ValueError("V4 PIT universe coverage does not match the frozen 60-day prediction window")
        tickers = set().union(*universe.values())
        buffer_start = start - timedelta(days=45)
        snapshots = [row[0] for row in connection.execute(
            "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date,market_snapshot_id",
            [market["source"], buffer_start, end]).fetchall()]
        bars = MarketDataStore(connection).load_bars_many_for_tickers(snapshots, tickers)
        days = sorted({bar.trade_date for bar in bars})
        if start not in days or end not in days:
            raise ValueError("frozen V4 market data cannot cover the scheduled prediction window")
        benchmark = _benchmark(Path(args.benchmark_csv), start, end)
        validate_benchmark_dates(benchmark, [day for day in days if start <= day <= end])
        suspended = TradingStatusStore(connection).suspended_tickers_by_snapshot_ids(
            engine["trading_status_snapshot_ids"], start, end)
        p, fee, ex, gap = replay["portfolio"], replay["fee_model"], replay["exit_rule"], replay["open_gap_gate"]
        spec = StrategySpec("ml_ridge_v4_minimal_validation_v1", "ridge_v1", ML_RIDGE_PROVIDER_TYPE,
                            json.dumps({"top_n": int(replay["candidate_top_n"]), "schedule_sha256": schedule.artifact_sha256}, sort_keys=True))
        provider = ScheduledMLRidgeScoreProvider(spec, schedule)
        policy = PortfolioPolicy.fixed_target_notional(Decimal(p["target_notional_per_position"]), int(p["max_positions"]), int(p["lot_size"]))
        costs = FeeModel(fee["version"], Decimal(fee["commission_rate"]), Decimal(fee["minimum_commission"]), _sell_only(fee["stamp_duty_rate"]), Decimal(fee["transfer_fee_rate"]), Decimal(fee["slippage_rate"]))
        exits = ExitRule(Decimal(ex["stop_loss_rate"]), Decimal(ex["take_profit_min_rate"]), Decimal(ex["trailing_drawdown_rate"]), int(ex["max_holding_days"]))
        SettlementService(connection).create_account(args.account_id, "Ridge V4 minimal validation", Decimal(replay["initial_cash"]), start)
        result = replay_daily_strategy(connection, bars, days, BacktestConfig(args.account_id, start, end, int(p["lot_size"]), int(engine["feature_lookback_days"]), int(replay["candidate_top_n"]), Decimal("1"), spec, policy, OpenGapPolicy(Decimal(gap["max_gap_up"]), Decimal(gap["max_gap_down"]))), costs, exits, universe_by_date=universe, score_provider=provider, suspended_tickers_by_date=suspended)
        metrics = calculate_metrics(connection, args.account_id, benchmark)
        nav_count, nav_start, nav_end = connection.execute("SELECT count(*),min(trade_date),max(trade_date) FROM sim_nav_daily WHERE account_id=?", [args.account_id]).fetchone()
        if (nav_count, nav_start, nav_end) != (60, start, end):
            raise ValueError("Ridge validation NAV is not continuous across the scheduled window")
        report = {"schema_version": "ridge_v4_minimal_validation_v1", "status": "COMPLETED_NOT_COMPARABLE_TO_FULL_V4",
                  "account_id": args.account_id, "prediction_dates": [start.isoformat(), end.isoformat()],
                  "profile": expected_profile, "dataset_sha256": _sha(dataset), "dataset_manifest_sha256": _sha(manifest),
                  "schedule_sha256": schedule.artifact_sha256, "model_sha256": entry.model_sha256,
                  "training_decision_dates": [entry.training_decision_start.isoformat(), entry.training_decision_end.isoformat()],
                  "label_availability_cutoff_date": entry.label_availability_cutoff_date.isoformat(),
                  "replay": result, "metrics": metrics, "nav": {"count": nav_count, "start": str(nav_start), "end": str(nav_end)},
                  "created_at": datetime.now(timezone.utc).isoformat()}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"account_id": args.account_id, "report": str(output), "metrics": metrics}, ensure_ascii=False, default=str))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
