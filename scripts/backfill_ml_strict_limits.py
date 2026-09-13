"""Backfill authoritative Tushare limits for PIT ML constituents without mutating raw history."""

import argparse
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

from quant_core.database import read_connection, writer_connection

from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.models import DayBar
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.tushare_source import DailyLimit, fetch_daily_limit_records, merge_daily_limits


SHANGHAI = ZoneInfo("Asia/Shanghai")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--universe-group-name", default="historical_large_cap_momentum")
    parser.add_argument("--snapshot-prefix", default="ml_strict_limits")
    parser.add_argument("--artifact-dir", default="data/ml_history/strict_limits")
    parser.add_argument("--max-days", type=int, help="Bound one invocation; safe to resume later")
    parser.add_argument("--sleep-seconds", type=float, default=8.0)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end or args.max_days is not None and args.max_days < 1 or args.sleep_seconds < 0:
        raise ValueError("invalid date range, max-days, or sleep interval")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with read_connection(args.db) as connection:
        date_rows = connection.execute(
            "SELECT DISTINCT as_of_trade_date FROM universe_snapshots WHERE group_name = ? "
            "AND as_of_trade_date BETWEEN ? AND ? ORDER BY as_of_trade_date",
            [args.universe_group_name, start, end],
        ).fetchall()
    trade_dates = [row[0] for row in date_rows]
    with read_connection(args.db) as connection:
        trade_dates = [day for day in trade_dates if connection.execute(
            "SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?",
            [f"{args.snapshot_prefix}_{day:%Y%m%d}"],
        ).fetchone() is None]
    if args.max_days is not None:
        trade_dates = trade_dates[:args.max_days]
    if not trade_dates:
        raise ValueError("no point-in-time universe snapshots match the requested range")
    completed, skipped = [], []
    for index, trade_date in enumerate(trade_dates):
        snapshot_id = f"{args.snapshot_prefix}_{trade_date:%Y%m%d}"
        with read_connection(args.db) as connection:
            if connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone():
                skipped.append(trade_date.isoformat())
                continue
            members = [row[0] for row in connection.execute(
                "SELECT m.ticker FROM universe_snapshots u JOIN universe_members m "
                "ON m.universe_snapshot_id = u.universe_snapshot_id WHERE u.group_name = ? AND u.as_of_trade_date = ?",
                [args.universe_group_name, trade_date],
            ).fetchall()]
        if not members:
            raise RuntimeError(f"PIT universe is empty for {trade_date}")
        placeholders = ",".join("?" for _ in members)
        with read_connection(args.db) as connection:
            rows = connection.execute(
                "WITH chosen AS (SELECT b.*, ROW_NUMBER() OVER (PARTITION BY b.ticker ORDER BY s.created_at DESC, b.market_snapshot_id DESC) AS rn "
                "FROM daily_bars b JOIN market_data_snapshots s ON s.market_snapshot_id = b.market_snapshot_id "
                "WHERE b.trade_date = ? AND b.ticker IN (" + placeholders + ")) "
                "SELECT market_snapshot_id, trade_date, ticker, open, high, low, close, volume, amount, limit_up, limit_down, status "
                "FROM chosen WHERE rn = 1 ORDER BY ticker", [trade_date, *members],
            ).fetchall()
        if len(rows) != len(members):
            raise RuntimeError(f"historical OHLC is incomplete for {trade_date}: expected {len(members)}, got {len(rows)}")
        base_bars = [DayBar(*row[1:]) for row in rows]
        raw_limits = fetch_daily_limit_records(token, [trade_date])
        limits = [DailyLimit(trade_date, str(row["ts_code"]), Decimal(str(row["up_limit"])), Decimal(str(row["down_limit"])))
                  for row in raw_limits if str(row["ts_code"]) in set(members)]
        enriched = merge_daily_limits(base_bars, limits)
        missing = [bar.ticker for bar in enriched if bar.limit_up is None or bar.limit_down is None]
        if missing:
            raise RuntimeError(f"Tushare stk_limit is incomplete for {trade_date}: {missing[:5]}")
        raw_path = artifact_dir / f"{snapshot_id}.raw.json"
        raw_path.write_text(json.dumps(raw_limits, ensure_ascii=False, default=str, sort_keys=True), encoding="utf-8")
        manifest_path = artifact_dir / f"{snapshot_id}.manifest.json"
        manifest_hash = write_manifest(manifest_path, {str(raw_path): sha256(raw_path.read_bytes()).hexdigest()})
        published_at = datetime.combine(trade_date, time(17), tzinfo=SHANGHAI)
        with writer_connection(args.db) as connection:
            if connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone():
                skipped.append(trade_date.isoformat())
                continue
            store, snapshots = MarketDataStore(connection), SnapshotService(connection)
            snapshots.register_market_snapshot(snapshot_id, trade_date, "tushare_stk_limit_ml_strict", published_at,
                                               datetime.now(SHANGHAI), str(manifest_path), manifest_hash, datetime.now(SHANGHAI))
            store.store_bars(snapshot_id, enriched)
        completed.append({"trade_date": trade_date.isoformat(), "snapshot_id": snapshot_id, "tickers": len(enriched)})
        if index + 1 < len(trade_dates) and args.sleep_seconds:
            sleep(args.sleep_seconds)
    print(json.dumps({"completed": completed, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
