"""Capture Tushare real-time minute bars for simulated intraday monitoring.

This script never submits orders.  It archives each vendor response before
writing its bars, so minute indicators can be replayed with the same
point-in-time visibility that existed during shadow monitoring.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from os import environ
from pathlib import Path
from uuid import uuid4

from quant_core.database import writer_connection
from quant_core.execution_data import ExecutionMarketDataStore
from quant_core.tushare_minute_source import fetch_realtime_minute_records, realtime_minute_records_to_bars


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--tickers", required=True, help="Comma-separated Tushare tickers, e.g. 600000.SH,000001.SZ")
    parser.add_argument("--frequency", choices=("1min", "5min", "15min", "60min"), default="1min")
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--artifact-dir", default="data/intraday_snapshots")
    args = parser.parse_args()

    token = environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    tickers = tuple(value.strip() for value in args.tickers.split(",") if value.strip())
    records = fetch_realtime_minute_records(token, tickers, args.frequency)
    bars = realtime_minute_records_to_bars(args.frequency, records)
    if not bars:
        raise RuntimeError("Tushare returned no real-time minute bars; no snapshot was created")

    snapshot_id, received_at = args.snapshot_id or str(uuid4()), datetime.now(timezone.utc)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"tushare_rt_min_{snapshot_id}.raw.json"
    raw_path.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    manifest_path = artifact_dir / f"tushare_rt_min_{snapshot_id}.manifest.json"
    manifest_path.write_text(json.dumps({"files": {str(raw_path): raw_hash}}, sort_keys=True), encoding="utf-8")
    manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()
    with writer_connection(args.db) as connection:
        connection.execute(
            "INSERT INTO intraday_market_snapshots VALUES (?, ?, ?, 'tushare_rt_min', ?, ?, ?, ?, ?)",
            [snapshot_id, max(bar.bar_end_at for bar in bars).date(), int(args.frequency.removesuffix("min")),
             received_at, received_at, str(manifest_path), manifest_hash, received_at],
        )
        ExecutionMarketDataStore(connection).store_intraday_bars(snapshot_id, bars)
    print(snapshot_id)


if __name__ == "__main__":
    main()
