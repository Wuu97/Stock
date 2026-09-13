"""Replay a frozen ML Ridge artifact through the shared strict execution engine."""

import argparse
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from quant_core.database import writer_connection

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.market_data import MarketDataStore
from quant_core.matching import OpenGapPolicy
from quant_core.ml_shadow import MLShadowModel
from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule
from quant_core.settlement import SettlementService
from quant_core.strategy_research import MLRidgeScoreProvider, ml_ridge_strategy_spec
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--model", required=True)
    universe = parser.add_mutually_exclusive_group()
    universe.add_argument("--universe-snapshot-id")
    universe.add_argument("--universe-group-name", help="Point-in-time group required for historical comparison")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--cash-reserve", default="0.05")
    parser.add_argument("--max-open-gap-up", default="0.03")
    parser.add_argument("--max-open-gap-down", default="-0.04")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    model_path = Path(args.model)
    model = MLShadowModel.load(model_path)
    if model.trained_through_date >= start:
        raise ValueError("strict ML backtest must start after the model training cutoff")
    connection = writer_connection(args.db, transaction=False)
    try:
        if connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [args.account_id]).fetchone() is not None:
            raise ValueError("account already exists; use a new account id for an immutable replay")
        SettlementService(connection).create_account(args.account_id, "ML Ridge 严格回测账户", Decimal(args.initial_cash), start)
        spec = ml_ridge_strategy_spec(str(model_path), model.artifact_sha256, args.top_n)
        bars = MarketDataStore(connection).load_bars_many(args.market_snapshot_id)
        universe_by_date = None
        if args.universe_group_name:
            rows = connection.execute(
                "SELECT u.as_of_trade_date, m.ticker FROM universe_snapshots u JOIN universe_members m "
                "ON m.universe_snapshot_id = u.universe_snapshot_id WHERE u.group_name = ? "
                "AND u.as_of_trade_date BETWEEN ? AND ?", [args.universe_group_name, start, end]
            ).fetchall()
            universe_by_date = {}
            for trade_date, ticker in rows:
                universe_by_date.setdefault(trade_date, set()).add(ticker)
            if not universe_by_date:
                raise ValueError("no point-in-time universe members match the requested range")
        result = replay_daily_strategy(
            connection, bars, sorted({bar.trade_date for bar in bars}),
            BacktestConfig(args.account_id, start, end, top_n=args.top_n, strategy_spec=spec,
                           portfolio_policy=PortfolioPolicy.equal_weight(args.max_positions, Decimal(args.cash_reserve)),
                           open_gap_policy=OpenGapPolicy(Decimal(args.max_open_gap_up), Decimal(args.max_open_gap_down))),
            FeeModel("cost_a_share_2026_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0.001")),
            ExitRule(), None if not args.universe_snapshot_id else UniverseService(connection).member_tickers(args.universe_snapshot_id),
            universe_by_date=universe_by_date, score_provider=MLRidgeScoreProvider(spec, model),
        )
        nav = connection.execute("SELECT total_equity, max_drawdown FROM sim_nav_daily WHERE account_id = ? ORDER BY trade_date DESC LIMIT 1", [args.account_id]).fetchone()
    finally:
        connection.close()
    print(json.dumps({"replay": result, "final_total_equity": str(nav[0]) if nav else None,
                      "max_drawdown": str(nav[1]) if nav else None, "model_sha256": sha256(model_path.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
