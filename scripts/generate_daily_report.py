"""Write a daily monitoring report from immutable runs, orders and NAV records."""

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from quant_core.reporting import daily_monitoring_summary, write_daily_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--account-id")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    connection = duckdb.connect(args.db)
    summary = daily_monitoring_summary(connection, date.fromisoformat(args.trade_date), args.account_id)
    connection.close()
    write_daily_report(Path(args.output), summary, datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
