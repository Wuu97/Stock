"""Create and fund a simulated account once before daily settlement."""

import argparse
from datetime import date
from decimal import Decimal

import duckdb

from quant_core.settlement import SettlementService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--initial-cash", required=True)
    parser.add_argument("--opening-date", required=True)
    args = parser.parse_args()
    connection = duckdb.connect(args.db)
    SettlementService(connection).create_account(
        args.account_id, args.name, Decimal(args.initial_cash), date.fromisoformat(args.opening_date)
    )
    connection.close()


if __name__ == "__main__":
    main()
