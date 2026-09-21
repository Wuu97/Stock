"""Build a new-schema PIT dataset with independent T+5/T+10/T+20 labels.

This never overwrites the legacy Ridge T+5 dataset.  The output is intended
for P1 research and must be written to a new immutable path.
"""

import argparse
import csv
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import duckdb

from quant_core.market_data import MarketDataStore
from quant_core.ml_dataset import build_multihorizon_cross_sectional_dataset
from quant_core.prediction_labels import LABEL_SCHEMA_VERSION, PredictionTarget
from quant_core.snapshots import canonical_hash


def _parse_day(value):
    return datetime.strptime(value, "%Y%m%d").date() if len(value) == 8 and value.isdigit() else date.fromisoformat(value)


def _read_adjusted(path, allowed=None):
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not {"trade_date", "ticker", "adj_close"}.issubset(reader.fieldnames):
            raise ValueError("adjusted CSV must contain trade_date,ticker,adj_close")
        return {(_parse_day(row["trade_date"]), row["ticker"]): Decimal(row["adj_close"])
                for row in reader if allowed is None or row["ticker"] in allowed}


def _column_type(name):
    if name == "trade_date" or name.endswith("_label_available_trade_date"):
        return "DATE"
    if name.endswith("_label_status"):
        return "VARCHAR"
    if name.endswith("_is_up"):
        return "BOOLEAN"
    if name in {"ticker"}:
        return "VARCHAR"
    return "DOUBLE"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--market-snapshot-prefix", required=True)
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--adjusted-csv", required=True)
    parser.add_argument("--benchmark-adjusted-csv", required=True)
    parser.add_argument("--benchmark", default="000300.SH")
    parser.add_argument("--horizons", default="5,10,20")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists() or output.with_suffix(output.suffix + ".manifest.json").exists():
        raise FileExistsError("prediction dataset artifacts are immutable; choose a new output path")
    horizons = tuple(int(value) for value in args.horizons.split(",") if value.strip())
    targets = tuple(PredictionTarget(value) for value in horizons)
    if len(horizons) != len(targets) or len(set(horizons)) != len(horizons):
        raise ValueError("horizons must be unique positive integers")
    connection = duckdb.connect(args.db, read_only=True)
    try:
        snapshots = [row[0] for row in connection.execute(
            "SELECT market_snapshot_id FROM market_data_snapshots WHERE market_snapshot_id LIKE ? ORDER BY trade_date",
            [args.market_snapshot_prefix + "%"],
        ).fetchall()]
        universe_rows = connection.execute(
            "SELECT u.as_of_trade_date,m.ticker FROM universe_snapshots u JOIN universe_members m "
            "ON m.universe_snapshot_id=u.universe_snapshot_id WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ?",
            [args.group_name, args.start_date, args.end_date],
        ).fetchall()
        if not snapshots or not universe_rows:
            raise ValueError("market snapshots or PIT universe evidence is missing")
        allowed = {ticker for _, ticker in universe_rows}
        bars = MarketDataStore(connection).load_bars_many_for_tickers(snapshots, allowed)
        manifests = connection.execute(
            "SELECT market_snapshot_id,manifest_sha256 FROM market_data_snapshots WHERE market_snapshot_id IN (" +
            ",".join("?" for _ in snapshots) + ")", snapshots,
        ).fetchall()
    finally:
        connection.close()
    universe = {}
    for day, ticker in universe_rows:
        universe.setdefault(day, set()).add(ticker)
    adjusted_path, benchmark_path = Path(args.adjusted_csv), Path(args.benchmark_adjusted_csv)
    adjusted = _read_adjusted(adjusted_path, allowed)
    benchmark = {day: close for (day, ticker), close in _read_adjusted(benchmark_path, {args.benchmark}).items()
                 if ticker == args.benchmark}
    calendar = sorted({bar.trade_date for bar in bars if bar.ticker == args.benchmark} | set(benchmark))
    rows = build_multihorizon_cross_sectional_dataset(
        bars, calendar, universe, adjusted, benchmark, date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date), targets,
    )
    if not rows:
        raise ValueError("prediction dataset has no feature rows")
    payload_rows = [row.as_dict() for row in rows]
    columns = tuple(payload_rows[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = duckdb.connect(":memory:")
    try:
        writer.execute("CREATE TABLE dataset (" + ", ".join('"{}" {}'.format(name, _column_type(name)) for name in columns) + ")")
        writer.executemany("INSERT INTO dataset VALUES (" + ",".join("?" for _ in columns) + ")",
                           [tuple(row[name] for name in columns) for row in payload_rows])
        writer.execute("COPY dataset TO ? (FORMAT PARQUET)", [str(output)])
    finally:
        writer.close()
    manifest = {
        "schema_version": "prediction_dataset_v1", "label_schema_version": LABEL_SCHEMA_VERSION,
        "targets": [{"id": target.target_id, "horizon_days": target.horizon_days,
                     "upward_return_threshold": str(target.upward_return_threshold)} for target in targets],
        "label_price_basis": "SUPPLIER_ADJUSTED_CLOSE_V1", "benchmark_symbol": args.benchmark,
        "group_name": args.group_name, "rows": len(rows), "date_range": [args.start_date, args.end_date],
        "columns": list(columns), "adjusted_csv_sha256": sha256(adjusted_path.read_bytes()).hexdigest(),
        "benchmark_adjusted_csv_sha256": sha256(benchmark_path.read_bytes()).hexdigest(),
        "input_market_manifests": dict(manifests), "dataset_sha256": sha256(output.read_bytes()).hexdigest(),
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "output": str(output), "manifest": str(manifest_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
