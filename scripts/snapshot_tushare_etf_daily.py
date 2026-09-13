"""Archive Tushare ETF daily bars for research; these bars are not strict-execution eligible."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.environment import load_env_file
from quant_core.etf_source import fund_daily_to_bars
from quant_core.market_data import MarketDataStore
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.tushare_source import create_tushare_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--ticker", action="append", required=True, help="ETF ticker; repeat for each fund.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--artifact-dir", default="data/tushare_etf_snapshots")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end:
        raise ValueError("--start-date must not be after --end-date")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    client, raw_rows = create_tushare_client(token), []
    for ticker in sorted(set(args.ticker)):
        rows = client.fund_daily(ts_code=ticker, start_date=start.strftime("%Y%m%d"),
                                 end_date=end.strftime("%Y%m%d"), fields="ts_code,trade_date,open,high,low,close,vol,amount").to_dict("records")
        raw_rows.extend(rows)
    bars = fund_daily_to_bars(raw_rows)
    if not bars:
        raise RuntimeError("Tushare fund_daily returned no ETF bars")
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"tushare_etf_daily_{args.snapshot_id}.raw.json"
    raw_path.write_text(json.dumps(raw_rows, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    manifest_path = artifact_dir / f"market_{args.snapshot_id}.manifest.json"
    manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
    now = datetime.now(timezone.utc)
    with writer_connection(args.db) as connection:
        SnapshotService(connection).register_market_snapshot(args.snapshot_id, end, "tushare_fund_daily_research", now, now,
                                                             str(manifest_path), manifest_hash, now)
        MarketDataStore(connection).store_bars(args.snapshot_id, bars)
    print(json.dumps({"snapshot_id": args.snapshot_id, "bars": len(bars), "strict_execution_eligible": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
