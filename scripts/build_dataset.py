"""Build an immutable, PIT-safe ML research dataset from stored bars and adjusted closes."""

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Optional, Set

import duckdb

from quant_core.market_data import MarketDataStore
from quant_core.ml_dataset import build_cross_sectional_dataset
from quant_core.snapshots import canonical_hash


def _read_adjusted_closes(path: Path, allowed_tickers: Optional[Set[str]] = None) -> dict[tuple[date, str], Decimal]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        required = {"trade_date", "ticker", "adj_close"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("adjusted CSV must contain trade_date,ticker,adj_close")
        return {(_parse_trade_date(row["trade_date"]), row["ticker"]): Decimal(row["adj_close"])
                for row in reader if allowed_tickers is None or row["ticker"] in allowed_tickers}


def _parse_trade_date(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date() if len(value) == 8 and value.isdigit() else date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--market-snapshot-id", action="append")
    source.add_argument("--market-snapshot-prefix", help="Load every immutable snapshot whose ID starts with this prefix")
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--adjusted-csv", required=True, help="Raw supplier export with trade_date,ticker,adj_close")
    parser.add_argument("--benchmark-adjusted-csv", help="Optional separately archived benchmark adjusted-close CSV")
    parser.add_argument("--benchmark", default="000300.SH")
    parser.add_argument("--output", required=True, help="Parquet output path")
    args = parser.parse_args()

    connection = duckdb.connect(args.db, read_only=True)
    try:
        snapshot_ids = args.market_snapshot_id
        if args.market_snapshot_prefix:
            snapshot_ids = [row[0] for row in connection.execute(
                "SELECT market_snapshot_id FROM market_data_snapshots WHERE market_snapshot_id LIKE ? ORDER BY trade_date",
                [args.market_snapshot_prefix + "%"],
            ).fetchall()]
        if not snapshot_ids:
            raise ValueError("no market snapshots match the requested source")
        universe_rows = connection.execute(
            "SELECT u.as_of_trade_date, m.ticker FROM universe_snapshots u JOIN universe_members m "
            "ON m.universe_snapshot_id = u.universe_snapshot_id WHERE u.group_name = ? AND u.as_of_trade_date BETWEEN ? AND ?",
            [args.group_name, args.start_date, args.end_date],
        ).fetchall()
        allowed_tickers = {ticker for _, ticker in universe_rows}
        bars = MarketDataStore(connection).load_bars_many_for_tickers(snapshot_ids, allowed_tickers)
        manifests = connection.execute(
            "SELECT market_snapshot_id, manifest_sha256 FROM market_data_snapshots WHERE market_snapshot_id IN ("
            + ",".join("?" for _ in snapshot_ids) + ")", snapshot_ids,
        ).fetchall()
    finally:
        connection.close()
    universe_by_date: dict[date, set[str]] = {}
    for trade_date, ticker in universe_rows:
        universe_by_date.setdefault(trade_date, set()).add(ticker)
    adjusted_path = Path(args.adjusted_csv)
    adjusted = _read_adjusted_closes(adjusted_path, allowed_tickers)
    benchmark_path = Path(args.benchmark_adjusted_csv) if args.benchmark_adjusted_csv else adjusted_path
    benchmark_adjusted = _read_adjusted_closes(benchmark_path, {args.benchmark})
    benchmark = {day: close for (day, ticker), close in benchmark_adjusted.items() if ticker == args.benchmark}
    calendar = sorted({bar.trade_date for bar in bars if bar.ticker == args.benchmark} | set(benchmark))
    if not calendar:
        raise ValueError("benchmark calendar is missing")
    rows = build_cross_sectional_dataset(
        bars, calendar, universe_by_date, adjusted, benchmark,
        date.fromisoformat(args.start_date), date.fromisoformat(args.end_date),
    )
    if not rows:
        raise ValueError("dataset has no mature rows")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = duckdb.connect(":memory:")
    try:
        writer.execute("CREATE TABLE dataset AS SELECT * FROM (SELECT CAST(NULL AS DATE) trade_date, CAST(NULL AS VARCHAR) ticker, CAST(NULL AS DOUBLE) \"close\", CAST(NULL AS DOUBLE) momentum_5d, CAST(NULL AS DOUBLE) momentum_20d, CAST(NULL AS DOUBLE) sma20_deviation, CAST(NULL AS DOUBLE) volume_ratio_20d, CAST(NULL AS DOUBLE) momentum_20d_percentile, CAST(NULL AS DOUBLE) momentum_20d_zscore, CAST(NULL AS DOUBLE) target_excess_ret_5d, CAST(NULL AS VARCHAR) label_status) WHERE FALSE")
        writer.executemany("INSERT INTO dataset VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [tuple(row.as_dict().values()) for row in rows])
        writer.execute("COPY dataset TO '" + str(output).replace("'", "''") + "' (FORMAT PARQUET)")
    finally:
        writer.close()
    manifest = {
        "schema_version": "ml_dataset_v1", "label_price_basis": "SUPPLIER_ADJUSTED_CLOSE_V1",
        "trading_days_forward": 5, "benchmark_symbol": args.benchmark, "group_name": args.group_name,
        "rows": len(rows), "date_range": [args.start_date, args.end_date],
        "feature_columns": list(rows[0].as_dict().keys())[2:-2],
        "label_status_counts": {status: sum(row.label_status == status for row in rows) for status in sorted({row.label_status for row in rows})},
        "adjusted_csv_sha256": sha256(adjusted_path.read_bytes()).hexdigest(),
        "benchmark_adjusted_csv_sha256": sha256(benchmark_path.read_bytes()).hexdigest(),
        "input_market_manifests": dict(manifests), "dataset_sha256": sha256(output.read_bytes()).hexdigest(),
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    output.with_suffix(output.suffix + ".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "manifest": str(output.with_suffix(output.suffix + ".manifest.json"))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
