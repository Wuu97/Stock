"""Run one reproducible baseline experiment and archive its configuration and metrics."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.experiments import ExperimentSpec, ExperimentStore, calculate_metrics
from quant_core.market_data import MarketDataStore
from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule
from quant_core.settlement import SettlementService
from quant_core.strategy_research import baseline_strategy_spec
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--benchmark-ticker", required=True)
    parser.add_argument("--universe-snapshot-id")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--volume-multiple", default="1.0")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--portfolio-method", choices=("fixed_shares", "equal_weight"), default="fixed_shares")
    parser.add_argument("--shares-per-order", type=int, default=100)
    parser.add_argument("--max-positions", type=int)
    parser.add_argument("--cash-reserve", default="0")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    volume_multiple, reserve = Decimal(args.volume_multiple), Decimal(args.cash_reserve)
    policy = (PortfolioPolicy.fixed_shares(args.shares_per_order, args.max_positions)
              if args.portfolio_method == "fixed_shares"
              else PortfolioPolicy.equal_weight(args.max_positions or args.top_n, reserve))
    fee = FeeModel("cost_a_share_2026_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0.001"))
    initial_cash = Decimal(args.initial_cash)
    strategy = baseline_strategy_spec(volume_multiple, args.top_n)
    rule = ExitRule()
    connection = duckdb.connect(args.db)
    try:
        if connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [args.account_id]).fetchone():
            raise ValueError("experiment account_id already exists; experiments require a fresh account")
        bars = MarketDataStore(connection).load_bars_many(args.market_snapshot_id)
        days = sorted({bar.trade_date for bar in bars})
        allowed = None if not args.universe_snapshot_id else UniverseService(connection).member_tickers(args.universe_snapshot_id)
        universe_reference = args.universe_snapshot_id or "ALL_BARS_UNIVERSE"
        spec = ExperimentSpec(strategy, policy, rule, fee.version, start, end, tuple(args.market_snapshot_id),
                              universe_reference, args.benchmark_ticker, initial_cash)
        now, experiment_id = datetime.now(timezone.utc), str(uuid4())
        SettlementService(connection).create_account(args.account_id, f"Experiment {experiment_id[:8]}", initial_cash, start)
        store = ExperimentStore(connection)
        store.create(experiment_id, args.account_id, spec, now)
        replay = replay_daily_strategy(connection, bars, days,
                                       BacktestConfig(args.account_id, start, end, args.shares_per_order, 20, args.top_n,
                                                      volume_multiple, strategy, policy), fee, rule, allowed)
        metrics = calculate_metrics(connection, args.account_id,
                                    [bar for bar in bars if bar.ticker == args.benchmark_ticker])
        store.store_result(experiment_id, metrics, datetime.now(timezone.utc))
    finally:
        connection.close()
    report = {"experiment_id": experiment_id, "spec": json.loads(spec.canonical_json()), "replay": replay, "metrics": metrics}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"experiment_id": experiment_id, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
