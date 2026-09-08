"""Create a new immutable daily-bar snapshot with raw Tushare historical limit prices."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

import duckdb

from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.tushare_source import DailyLimit, fetch_daily_limit_records, merge_daily_limits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--base-snapshot-id", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--artifact-dir", default="data/tushare_snapshots")
    args = parser.parse_args()

    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    connection = duckdb.connect(args.db)
    try:
        base_bars = MarketDataStore(connection).load_bars(args.base_snapshot_id)
        raw_rows = fetch_daily_limit_records(token, sorted({bar.trade_date for bar in base_bars}))
        required_keys = {
            (bar.trade_date.strftime("%Y%m%d"), bar.ticker) for bar in base_bars
        }
        relevant = [row for row in raw_rows if (str(row["trade_date"]), str(row["ts_code"])) in required_keys]
        limits = [DailyLimit(
            datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(), str(row["ts_code"]),
            Decimal(str(row["up_limit"])), Decimal(str(row["down_limit"])),
        ) for row in relevant]
        enriched = merge_daily_limits(base_bars, limits)
        missing = [bar for bar in enriched if bar.limit_up is None or bar.limit_down is None]
        if missing:
            raise RuntimeError(f"Tushare limits missing for {len(missing)} selected daily bars")

        artifact_dir = Path(args.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        raw_path = artifact_dir / f"tushare_limits_{args.snapshot_id}.raw.json"
        raw_path.write_text(json.dumps(relevant, ensure_ascii=False, default=str, sort_keys=True), encoding="utf-8")
        raw_hash = sha256(raw_path.read_bytes()).hexdigest()
        manifest_path = artifact_dir / f"market_{args.snapshot_id}.manifest.json"
        manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
        now = datetime.now(timezone.utc)
        trade_date = max(bar.trade_date for bar in enriched)
        SnapshotService(connection).register_market_snapshot(
            args.snapshot_id, trade_date, "tushare_stk_limit_enriched", now, now,
            str(manifest_path), manifest_hash, now,
        )
        MarketDataStore(connection).store_bars(args.snapshot_id, enriched)
    finally:
        connection.close()
    print(args.snapshot_id, len(enriched), len(relevant))


if __name__ == "__main__":
    main()
