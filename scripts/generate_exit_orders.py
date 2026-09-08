"""Generate next-open paper-trading sell orders from uniform daily exit rules."""

import argparse
from datetime import date
from decimal import Decimal
import json

import duckdb

from quant_core.daily_risk import create_exit_intents
from quant_core.market_data import MarketDataStore
from quant_core.risk import ExitRule


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True, action="append")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--next-trading-date", required=True)
    parser.add_argument("--stop-loss-rate", default="0.10")
    parser.add_argument("--take-profit-min-rate", default="0.15")
    parser.add_argument("--trailing-drawdown-rate", default="0.05")
    parser.add_argument("--max-holding-days", type=int, default=60)
    args = parser.parse_args()

    connection = duckdb.connect(args.db)
    try:
        signals = create_exit_intents(
            connection, args.account_id, date.fromisoformat(args.as_of_date),
            date.fromisoformat(args.next_trading_date),
            MarketDataStore(connection).load_bars_many(args.market_snapshot_id),
            ExitRule(Decimal(args.stop_loss_rate), Decimal(args.take_profit_min_rate),
                     Decimal(args.trailing_drawdown_rate), args.max_holding_days),
        )
    finally:
        connection.close()
    print(json.dumps(signals, ensure_ascii=False))


if __name__ == "__main__":
    main()
