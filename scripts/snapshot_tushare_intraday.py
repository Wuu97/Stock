"""Archive one Tushare historical-minute request as an immutable replay snapshot."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from os import environ
from pathlib import Path
from uuid import uuid4

from quant_core.database import writer_connection
from quant_core.execution_data import ExecutionMarketDataStore
from quant_core.tushare_minute_source import fetch_stock_minute_records, minute_records_to_bars


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--frequency", choices=("1min", "5min", "15min", "60min"), default="5min")
    parser.add_argument("--start-at", required=True, help="Timezone-aware ISO timestamp")
    parser.add_argument("--end-at", required=True, help="Timezone-aware ISO timestamp")
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--artifact-dir", default="data/intraday_snapshots")
    args = parser.parse_args()

    token = environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    start_at, end_at = datetime.fromisoformat(args.start_at), datetime.fromisoformat(args.end_at)
    records = fetch_stock_minute_records(token, args.ticker, args.frequency, start_at, end_at)
    bars = minute_records_to_bars(args.ticker, args.frequency, records)
    if not bars:
        raise RuntimeError("Tushare returned no minute bars; no snapshot was created")

    snapshot_id, now = args.snapshot_id or str(uuid4()), datetime.now(timezone.utc)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"tushare_{snapshot_id}.raw.json"
    raw_path.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    manifest_path = artifact_dir / f"tushare_{snapshot_id}.manifest.json"
    manifest_path.write_text(json.dumps({"files": {str(raw_path): raw_hash}}, sort_keys=True), encoding="utf-8")
    manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()
    with writer_connection(args.db) as connection:
        connection.execute(
            "INSERT INTO intraday_market_snapshots VALUES (?, ?, ?, 'tushare_stk_mins', ?, ?, ?, ?, ?)",
            [snapshot_id, bars[0].bar_end_at.date(), int(args.frequency.removesuffix("min")), now, now,
             str(manifest_path), manifest_hash, now],
        )
        ExecutionMarketDataStore(connection).store_intraday_bars(snapshot_id, bars)
    print(snapshot_id)


if __name__ == "__main__":
    main()
