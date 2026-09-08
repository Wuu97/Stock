"""Generate a compact JSON report from persisted recommendation evaluations."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from quant_core.evaluation import EvaluationService
from quant_core.market_data import MarketDataStore
from quant_core.reporting import write_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True)
    parser.add_argument("--benchmark-ticker", required=True)
    parser.add_argument("--evaluation-version", default="evaluation_v1")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    connection = duckdb.connect(args.db)
    summary = EvaluationService(connection).evaluate_run(
        args.run_id, MarketDataStore(connection).load_bars(args.market_snapshot_id),
        args.benchmark_ticker, args.evaluation_version,
    )
    write_report(Path(args.output), args.run_id, summary, datetime.now(timezone.utc))


if __name__ == "__main__":
    main()
