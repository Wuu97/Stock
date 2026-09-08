"""Load a validated local daily-bar CSV into one immutable market snapshot."""

import argparse
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import duckdb

from quant_core.market_data import MarketDataStore, read_daily_csv
from quant_core.snapshots import SnapshotService, write_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--source", default="local_csv")
    parser.add_argument("--published-at", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--audit-dir", default="data/audit")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    bars = read_daily_csv(csv_path)
    snapshot_id = args.snapshot_id or str(uuid4())
    published_at = datetime.fromisoformat(args.published_at)
    if published_at.tzinfo is None:
        raise ValueError("--published-at must include a timezone")
    digest = sha256(csv_path.read_bytes()).hexdigest()
    manifest_path = Path(args.audit_dir) / f"market_{snapshot_id}.json"
    manifest_hash = write_manifest(manifest_path, {str(csv_path): digest})
    connection = duckdb.connect(args.db)
    snapshots = SnapshotService(connection)
    snapshots.register_market_snapshot(snapshot_id, max(bar.trade_date for bar in bars), args.source,
                                       published_at, datetime.now(published_at.tzinfo), str(manifest_path),
                                       manifest_hash, datetime.now(published_at.tzinfo))
    MarketDataStore(connection).store_bars(snapshot_id, bars)
    connection.close()


if __name__ == "__main__":
    main()
