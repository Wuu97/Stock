"""Freeze a monitoring universe from a current market-cap snapshot and local daily bars."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal

import duckdb

from quant_core.market_data import MarketDataStore
from quant_core.universe import DynamicUniverseRule, UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--market-snapshot-id", required=True, help="Daily-bar snapshot used for 30-day return")
    parser.add_argument("--market-cap-snapshot-id", required=True, help="Current market-cap snapshot")
    parser.add_argument("--group-name", required=True, help="Stable, human-readable monitoring-group name")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--min-total-market-cap", default="80000000000", help="CNY; default is 80 billion")
    parser.add_argument("--momentum-days", type=int, default=30)
    parser.add_argument("--top-n", type=int, default=50)
    args = parser.parse_args()

    if args.momentum_days <= 0 or args.top_n <= 0:
        raise ValueError("--momentum-days and --top-n must be positive")
    connection = duckdb.connect(args.db)
    service = UniverseService(connection)
    rule = DynamicUniverseRule(args.group_name, Decimal(args.min_total_market_cap), args.momentum_days, args.top_n)
    snapshot_id = service.create_snapshot(
        args.market_cap_snapshot_id,
        date.fromisoformat(args.as_of_date),
        rule,
        MarketDataStore(connection).load_bars(args.market_snapshot_id),
        datetime.now(timezone.utc),
    )
    connection.close()
    print(snapshot_id)


if __name__ == "__main__":
    main()
