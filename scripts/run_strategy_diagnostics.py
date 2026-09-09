"""Write an auditable diagnostic report for one completed strategy experiment."""

import argparse
import csv
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import duckdb

from quant_core.experiments import BenchmarkClose
from quant_core.strategy_diagnostics import RegimeConfig, calculate_strategy_diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--benchmark-close-csv", required=True)
    parser.add_argument("--benchmark-ticker", default="000300.SH")
    parser.add_argument("--regime-lookback-days", type=int, default=60)
    parser.add_argument("--regime-threshold", default="0.05")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    benchmark = _read_benchmark(Path(args.benchmark_close_csv), args.benchmark_ticker)
    connection = duckdb.connect(args.db, read_only=True)
    try:
        report = calculate_strategy_diagnostics(
            connection, args.account_id, benchmark,
            RegimeConfig(args.regime_lookback_days, Decimal(args.regime_threshold)),
        )
    finally:
        connection.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output)}, ensure_ascii=False))


def _read_benchmark(path: Path, ticker: str) -> list[BenchmarkClose]:
    with path.open(encoding="utf-8", newline="") as source:
        rows = csv.DictReader(source)
        values = [BenchmarkClose(date.fromisoformat(row["trade_date"]), Decimal(row["adj_close"]))
                  for row in rows if row["ticker"] == ticker]
    if not values:
        raise ValueError("benchmark CSV has no records for ticker")
    return values


if __name__ == "__main__":
    main()
