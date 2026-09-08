"""Replay using the frozen universe snapshot that belongs to each historical trade date."""

import argparse
from datetime import date
from decimal import Decimal
import json

import duckdb

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.market_data import MarketDataStore
from quant_core.models import FeeModel
from quant_core.risk import ExitRule
from quant_core.settlement import SettlementService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--initial-cash", default="1000000")
    args = parser.parse_args()
    connection = duckdb.connect(args.db)
    try:
        bars = MarketDataStore(connection).load_bars_many(args.market_snapshot_id)
        universes = connection.execute(
            "SELECT u.as_of_trade_date, m.ticker FROM universe_snapshots u JOIN universe_members m "
            "ON m.universe_snapshot_id = u.universe_snapshot_id WHERE u.group_name = ? "
            "AND u.as_of_trade_date BETWEEN ? AND ?", [args.group_name, args.start_date, args.end_date]
        ).fetchall()
        by_day = {}
        for day, ticker in universes:
            by_day.setdefault(day, set()).add(ticker)
        if not by_day:
            raise ValueError("no point-in-time universe snapshots exist for the requested range")
        if connection.execute("SELECT 1 FROM sim_accounts WHERE account_id = ?", [args.account_id]).fetchone() is None:
            SettlementService(connection).create_account(args.account_id, "历史时点回测账户", Decimal(args.initial_cash), date.fromisoformat(args.start_date))
        result = replay_daily_strategy(
            connection, bars, sorted({bar.trade_date for bar in bars}),
            BacktestConfig(args.account_id, date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)),
            FeeModel("cost_a_share_2026_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0.001")),
            ExitRule(), universe_by_date=by_day,
        )
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
