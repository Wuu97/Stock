"""Archive free Eastmoney ETF daily history and join Tushare ETF limit prices."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.eastmoney_source import fetch_history_payload, parse_history_bars
from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.tushare_source import fetch_etf_limit_records, merge_daily_limits, row_to_daily_limit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--ticker", action="append", required=True, help="ETF ticker; repeat for each fund.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--snapshot-prefix", default="eastmoney_etf_history")
    parser.add_argument("--artifact-dir", default="data/eastmoney_etf_snapshots")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end:
        raise ValueError("--start-date must not be after --end-date")
    tickers = sorted(set(args.ticker))
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")

    raw_history = {ticker: fetch_history_payload(ticker, start, end) for ticker in tickers}
    bars = tuple(bar for ticker in tickers for bar in parse_history_bars(ticker, raw_history[ticker]))
    raw_limits = fetch_etf_limit_records(token, tickers, start, end)
    enriched = merge_daily_limits(bars, (row_to_daily_limit(row) for row in raw_limits))
    missing = [(bar.trade_date, bar.ticker) for bar in enriched if bar.limit_up is None or bar.limit_down is None]
    if missing:
        raise RuntimeError(f"Tushare etf_limit is missing {len(missing)} ETF daily limits, first: {missing[:5]}")

    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"eastmoney_etf_history_{start:%Y%m%d}_{end:%Y%m%d}.raw.json"
    raw_path.write_text(json.dumps({"eastmoney": raw_history, "tushare_etf_limit": raw_limits}, ensure_ascii=False,
                                   sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    now = datetime.now(timezone.utc)
    with writer_connection(args.db) as connection:
        store, snapshots = MarketDataStore(connection), SnapshotService(connection)
        by_day = {}
        for bar in enriched:
            by_day.setdefault(bar.trade_date, []).append(bar)
        for trade_date, day_bars in sorted(by_day.items()):
            snapshot_id = f"{args.snapshot_prefix}_{trade_date:%Y%m%d}"
            if connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone():
                raise ValueError(f"snapshot already exists: {snapshot_id}")
            manifest_path = artifact_dir / f"market_{snapshot_id}.manifest.json"
            manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
            snapshots.register_market_snapshot(snapshot_id, trade_date, "eastmoney_etf_history_with_tushare_limits",
                                               now, now, str(manifest_path), manifest_hash, now)
            store.store_bars(snapshot_id, day_bars)
    print(json.dumps({"tickers": tickers, "bars": len(enriched), "days": len(by_day),
                      "source": "Eastmoney OHLCV + Tushare etf_limit"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
