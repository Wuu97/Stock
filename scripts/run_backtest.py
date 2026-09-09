"""Run the strict daily-bar replay against one or more immutable market snapshots."""

import argparse
from datetime import date
from decimal import Decimal
import json

import duckdb

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.market_data import MarketDataStore
from quant_core.models import FeeModel
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--universe-snapshot-id")
    parser.add_argument("--initial-cash", default="1000000")
    parser.add_argument("--portfolio-method", choices=("fixed_shares", "equal_weight"), default="fixed_shares")
    parser.add_argument("--shares-per-order", type=int, default=100)
    parser.add_argument("--max-positions", type=int)
    parser.add_argument("--cash-reserve", default="0")
    args = parser.parse_args()

    connection = duckdb.connect(args.db)
    try:
        bars = MarketDataStore(connection).load_bars_many(args.market_snapshot_id)
        days = sorted({bar.trade_date for bar in bars})
        if connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [args.account_id]).fetchone() is None:
            from quant_core.settlement import SettlementService
            SettlementService(connection).create_account(args.account_id, "历史回测账户", Decimal(args.initial_cash), date.fromisoformat(args.start_date))
        allowed = None if not args.universe_snapshot_id else UniverseService(connection).member_tickers(args.universe_snapshot_id)
        policy = (PortfolioPolicy.fixed_shares(args.shares_per_order, args.max_positions)
                  if args.portfolio_method == "fixed_shares"
                  else PortfolioPolicy.equal_weight(args.max_positions or 5, Decimal(args.cash_reserve)))
        result = replay_daily_strategy(
            connection, bars, days,
            BacktestConfig(args.account_id, date.fromisoformat(args.start_date), date.fromisoformat(args.end_date),
                           shares_per_order=args.shares_per_order, portfolio_policy=policy),
            FeeModel("cost_a_share_2026_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0.001")),
            ExitRule(), allowed,
        )
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
