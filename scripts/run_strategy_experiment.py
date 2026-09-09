"""Run one reproducible baseline experiment and archive its configuration and metrics."""

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Optional
from uuid import uuid4

import duckdb

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.experiments import BenchmarkClose, ExperimentSpec, ExperimentStore, calculate_metrics
from quant_core.market_data import MarketDataStore
from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule
from quant_core.settlement import SettlementService
from quant_core.strategy_research import baseline_strategy_spec, momentum_volume_strategy_spec, pure_momentum_strategy_spec
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    market_input = parser.add_mutually_exclusive_group(required=True)
    market_input.add_argument("--market-snapshot-id", action="append")
    market_input.add_argument("--market-source-channel")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--benchmark-ticker", required=True)
    parser.add_argument("--benchmark-close-csv", help="Archived real benchmark closes with trade_date,ticker,adj_close columns")
    universe_input = parser.add_mutually_exclusive_group()
    universe_input.add_argument("--universe-snapshot-id")
    universe_input.add_argument("--universe-group-name")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--volume-multiple", default="1.0")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--strategy", choices=("baseline", "pure_momentum", "momentum_volume"), default="baseline")
    parser.add_argument("--momentum-weight", default="0.7")
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
    strategy = ({
        "baseline": lambda: baseline_strategy_spec(volume_multiple, args.top_n),
        "pure_momentum": lambda: pure_momentum_strategy_spec(args.top_n),
        "momentum_volume": lambda: momentum_volume_strategy_spec(args.top_n, Decimal(args.momentum_weight)),
    }[args.strategy])()
    rule = ExitRule()
    connection = duckdb.connect(args.db)
    try:
        if connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [args.account_id]).fetchone():
            raise ValueError("experiment account_id already exists; experiments require a fresh account")
        snapshot_ids = args.market_snapshot_id or [snapshot_id for snapshot_id, in connection.execute(
            "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel = ? "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [args.market_source_channel, start, end],
        ).fetchall()]
        if not snapshot_ids:
            raise ValueError("no market snapshots match the experiment range")
        universe_service = UniverseService(connection)
        allowed = None if not args.universe_snapshot_id else universe_service.member_tickers(args.universe_snapshot_id)
        universe_by_date = (None if not args.universe_group_name
                            else universe_service.members_by_trade_date(args.universe_group_name, start, end))
        universe_reference = (f"PIT_GROUP:{args.universe_group_name}" if args.universe_group_name
                              else args.universe_snapshot_id or "ALL_BARS_UNIVERSE")
        benchmark_bars, benchmark_reference = _load_benchmark(args.benchmark_close_csv, args.benchmark_ticker)
        required_tickers = set() if benchmark_bars else {args.benchmark_ticker}
        if allowed is not None:
            required_tickers.update(allowed)
        if universe_by_date is not None:
            required_tickers.update(ticker for members in universe_by_date.values() for ticker in members)
        data_store = MarketDataStore(connection)
        bars = (data_store.load_bars_many_for_tickers(snapshot_ids, required_tickers)
                if required_tickers != {args.benchmark_ticker} else data_store.load_bars_many(snapshot_ids))
        days = sorted({bar.trade_date for bar in bars})
        spec = ExperimentSpec(strategy, policy, rule, fee.version, start, end, tuple(snapshot_ids),
                              universe_reference, args.benchmark_ticker, benchmark_reference, initial_cash)
        now, experiment_id = datetime.now(timezone.utc), str(uuid4())
        try:
            SettlementService(connection).create_account(args.account_id, f"Experiment {experiment_id[:8]}", initial_cash, start)
            store = ExperimentStore(connection)
            store.create(experiment_id, args.account_id, spec, now)
            replay = replay_daily_strategy(connection, bars, days,
                                           BacktestConfig(args.account_id, start, end, args.shares_per_order, 20, args.top_n,
                                                          volume_multiple, strategy, policy), fee, rule, allowed, universe_by_date)
            metrics = calculate_metrics(connection, args.account_id,
                                        benchmark_bars or [bar for bar in bars if bar.ticker == args.benchmark_ticker])
            store.store_result(experiment_id, metrics, datetime.now(timezone.utc))
        except Exception:
            _discard_failed_experiment(connection, args.account_id)
            raise
    finally:
        connection.close()
    report = {"experiment_id": experiment_id, "spec": json.loads(spec.canonical_json()), "replay": replay, "metrics": metrics}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"experiment_id": experiment_id, "output": str(output)}, ensure_ascii=False))


def _discard_failed_experiment(connection, account_id: str) -> None:
    """Remove only an experiment account after an orchestration failure.

    Settlement owns its own short ACID transactions, so DuckDB cannot nest a
    second transaction around the whole replay. This compensating cleanup keeps
    a failed research run from becoming an auditable-looking partial account.
    """
    experiment_ids = [row[0] for row in connection.execute(
        "SELECT experiment_id FROM strategy_experiments WHERE account_id = ?", [account_id]
    ).fetchall()]
    if experiment_ids:
        placeholders = ",".join("?" for _ in experiment_ids)
        connection.execute("DELETE FROM strategy_experiment_results WHERE experiment_id IN (" + placeholders + ")", experiment_ids)
    connection.execute("DELETE FROM strategy_experiments WHERE account_id = ?", [account_id])
    lot_ids = [row[0] for row in connection.execute(
        "SELECT lot_id FROM sim_position_lots WHERE account_id = ?", [account_id]
    ).fetchall()]
    if lot_ids:
        placeholders = ",".join("?" for _ in lot_ids)
        connection.execute("DELETE FROM sim_lot_disposal_events WHERE lot_id IN (" + placeholders + ")", lot_ids)
        connection.execute("DELETE FROM sim_lot_adjustment_events WHERE lot_id IN (" + placeholders + ")", lot_ids)
    for table in ("sim_dividend_entitlements", "sim_exit_signals", "sim_positions_daily", "sim_nav_daily", "sim_executions", "sim_order_intents"):
        connection.execute(f"DELETE FROM {table} WHERE account_id = ?", [account_id])
    connection.execute("DELETE FROM sim_position_lots WHERE account_id = ?", [account_id])
    connection.execute("DELETE FROM ledger_journal_entries WHERE account_id = ?", [account_id])
    connection.execute("DELETE FROM sim_accounts WHERE account_id = ?", [account_id])


def _load_benchmark(path_value: Optional[str], ticker: str) -> tuple[list[BenchmarkClose], str]:
    if path_value is None:
        return [], "MARKET_SNAPSHOTS"
    path = Path(path_value)
    with path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    required = {"trade_date", "ticker", "adj_close"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("benchmark CSV is missing trade_date,ticker,adj_close columns")
    values = [BenchmarkClose(date.fromisoformat(row["trade_date"]), Decimal(row["adj_close"]))
              for row in rows if row["ticker"] == ticker]
    if not values or any(value.close <= 0 for value in values):
        raise ValueError("benchmark CSV has no valid closes for the requested ticker")
    return values, f"CSV_SHA256:{sha256(path.read_bytes()).hexdigest()}"


if __name__ == "__main__":
    main()
