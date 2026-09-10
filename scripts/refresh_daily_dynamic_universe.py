"""Refresh the daily large-cap momentum universe from raw Tushare data."""

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

import duckdb
import tushare as ts

from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.models import DayBar
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.universe import DynamicUniverseRule, UniverseService


def _bars(rows):
    return [DayBar(
        datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(), str(row["ts_code"]),
        Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])), Decimal(str(row["close"])),
        int(Decimal(str(row["vol"])) * 100), Decimal(str(row["amount"])) * 1000, None, None,
    ) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--group-name", default="all_a_large_cap_momentum")
    parser.add_argument("--min-total-market-cap", default="80000000000")
    parser.add_argument("--momentum-days", type=int, default=30)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--benchmark-ticker", default="000300.SH")
    parser.add_argument("--include-ticker", action="append", default=[],
                        help="Also retain held or pending symbols needed for settlement and valuation.")
    parser.add_argument("--artifact-dir", default="data/daily_refresh")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    client = ts.pro_api(token)
    cap_frame = client.daily_basic(trade_date=trade_date.strftime("%Y%m%d"), fields="ts_code,trade_date,total_mv")
    minimum = Decimal(args.min_total_market_cap)
    caps = {str(row.ts_code): Decimal(str(row.total_mv)) * Decimal("10000") for row in cap_frame.itertuples(index=False)
            if Decimal(str(row.total_mv)) * Decimal("10000") >= minimum}
    if not caps:
        raise RuntimeError("no qualifying market-cap records returned")
    calendar = client.trade_cal(exchange="SSE", start_date=(trade_date - timedelta(days=70)).strftime("%Y%m%d"),
                                end_date=trade_date.strftime("%Y%m%d"), is_open="1")
    days = sorted(calendar["cal_date"].tolist())[-(args.momentum_days + 1):]
    if len(days) < args.momentum_days + 1:
        raise RuntimeError("insufficient trading days for momentum window")
    selected_tickers = set(caps) | set(args.include_ticker)
    records = []
    for day in days:
        frame = client.daily(trade_date=str(day), fields="ts_code,trade_date,open,high,low,close,vol,amount")
        records.extend(row for row in frame.to_dict("records") if str(row["ts_code"]) in selected_tickers)
    benchmark = client.index_daily(ts_code=args.benchmark_ticker, start_date=str(days[0]), end_date=str(days[-1]),
                                   fields="ts_code,trade_date,open,high,low,close,vol,amount")
    benchmark_rows = benchmark.to_dict("records")
    if len(benchmark_rows) != len(days):
        raise RuntimeError("benchmark index data is incomplete for the momentum window")
    records.extend(benchmark_rows)
    artifact_dir = Path(args.artifact_dir); artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"refresh_{trade_date.isoformat()}.raw.json"
    raw_path.write_text(json.dumps({"caps": cap_frame.to_dict("records"), "bars": records}, default=str, ensure_ascii=False), encoding="utf-8")
    manifest = artifact_dir / f"refresh_{trade_date.isoformat()}.manifest.json"
    manifest_hash = write_manifest(manifest, {str(raw_path): sha256(raw_path.read_bytes()).hexdigest()})
    snapshot_id, cap_id = f"tushare_pool_{trade_date:%Y%m%d}", f"tushare_cap_{trade_date:%Y%m%d}"
    now = datetime.now(timezone.utc)
    connection = duckdb.connect(args.db)
    try:
        snapshots, universes = SnapshotService(connection), UniverseService(connection)
        snapshots.register_market_snapshot(snapshot_id, trade_date, "tushare_daily", now, now, str(manifest), manifest_hash, now)
        MarketDataStore(connection).store_bars(snapshot_id, _bars(records))
        universes.store_market_caps(cap_id, now, "tushare_daily_basic", caps, now)
        universe_id = universes.create_snapshot(cap_id, trade_date, DynamicUniverseRule(
            args.group_name, minimum, args.momentum_days, args.top_n), MarketDataStore(connection).load_bars(snapshot_id), now)
    finally:
        connection.close()
    print(json.dumps({"market_snapshot_id": snapshot_id, "market_cap_snapshot_id": cap_id, "universe_snapshot_id": universe_id, "candidates": len(caps), "bars": len(records)}))


if __name__ == "__main__":
    main()
