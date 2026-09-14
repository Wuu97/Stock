"""Refresh the daily large-cap momentum universe from raw Tushare data."""

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

import tushare as ts

from quant_core.database import read_connection, writer_connection
from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.models import DayBar
from quant_core.snapshots import SnapshotService, daily_market_close_timestamp, write_manifest
from quant_core.universe import DynamicUniverseRule, UniverseService


def _bars(rows):
    return [DayBar(
        datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(), str(row["ts_code"]),
        Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])), Decimal(str(row["close"])),
        int(Decimal(str(row["vol"])) * 100), Decimal(str(row["amount"])) * 1000, None, None,
    ) for row in rows]


def _previous_refresh(connection):
    """Return the latest successful Top50 market snapshot for incremental refreshes."""
    return connection.execute(
        "SELECT market_snapshot_id, trade_date, manifest_sha256 FROM market_data_snapshots "
        "WHERE market_snapshot_id LIKE 'tushare_pool_%' ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()


def _local_window_bars(connection, tickers, days):
    """Read the locally retained rolling window, preferring the newest snapshot per ticker/day."""
    if not tickers or not days:
        return []
    ticker_marks = ",".join("?" for _ in tickers)
    day_marks = ",".join("?" for _ in days)
    rows = connection.execute(
        "SELECT trade_date, ticker, open, high, low, close, volume, amount, limit_up, limit_down, status "
        "FROM daily_bars WHERE ticker IN (" + ticker_marks + ") AND trade_date IN (" + day_marks + ") "
        "ORDER BY market_snapshot_id", [*tickers, *days]
    ).fetchall()
    by_key = {}
    for row in rows:
        by_key[(row[0], row[1])] = DayBar(*row)
    return list(by_key.values())


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
    days = sorted(datetime.strptime(str(value), "%Y%m%d").date() for value in calendar["cal_date"].tolist())[-(args.momentum_days + 1):]
    if len(days) < args.momentum_days + 1:
        raise RuntimeError("insufficient trading days for momentum window")
    selected_tickers = set(caps) | set(args.include_ticker)
    with read_connection(args.db) as connection:
        previous = _previous_refresh(connection)
        previous_date = previous[1] if previous else None
        previous_hash = previous[2] if previous else ""
        required_days = days if previous_date is None or previous_date < days[0] else [day for day in days if day > previous_date]
    records = []
    for day in required_days:
        frame = client.daily(trade_date=day.strftime("%Y%m%d"), fields="ts_code,trade_date,open,high,low,close,vol,amount")
        records.extend(row for row in frame.to_dict("records") if str(row["ts_code"]) in selected_tickers)
    with read_connection(args.db) as connection:
        existing = _local_window_bars(connection, sorted(selected_tickers | {args.benchmark_ticker}), days)
    existing_benchmark_dates = {bar.trade_date for bar in existing if bar.ticker == args.benchmark_ticker}
    benchmark_days = [day for day in days if day not in existing_benchmark_dates]
    benchmark = client.index_daily(ts_code=args.benchmark_ticker,
                                   start_date=min(benchmark_days).strftime("%Y%m%d"), end_date=max(benchmark_days).strftime("%Y%m%d"),
                                   fields="ts_code,trade_date,open,high,low,close,vol,amount") if benchmark_days else None
    benchmark_rows = [] if benchmark is None else benchmark.to_dict("records")
    if benchmark_days and len(benchmark_rows) != len(benchmark_days):
        raise RuntimeError("benchmark index data is incomplete for the momentum window")
    records.extend(benchmark_rows)
    refreshed = _bars(records)
    by_key = {(bar.trade_date, bar.ticker): bar for bar in existing}
    by_key.update({(bar.trade_date, bar.ticker): bar for bar in refreshed})
    rolling_bars = [bar for key, bar in by_key.items() if key[0] in set(days) and key[1] in (selected_tickers | {args.benchmark_ticker})]
    artifact_dir = Path(args.artifact_dir); artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"refresh_{trade_date.isoformat()}.raw.json"
    raw_path.write_text(json.dumps({"caps": cap_frame.to_dict("records"), "new_bars": records,
                                    "incremental_dates": [str(day) for day in required_days]}, default=str, ensure_ascii=False), encoding="utf-8")
    manifest = artifact_dir / f"refresh_{trade_date.isoformat()}.manifest.json"
    manifest_hash = write_manifest(manifest, {str(raw_path): sha256(raw_path.read_bytes()).hexdigest()}, previous_hash)
    snapshot_id, cap_id = f"tushare_pool_{trade_date:%Y%m%d}", f"tushare_cap_{trade_date:%Y%m%d}"
    now = datetime.now(timezone.utc)
    published_at = daily_market_close_timestamp(trade_date)
    with writer_connection(args.db) as connection:
        snapshots, universes = SnapshotService(connection), UniverseService(connection)
        snapshots.register_market_snapshot(snapshot_id, trade_date, "tushare_daily", published_at, now,
                                           str(manifest), manifest_hash, now)
        MarketDataStore(connection).store_bars(snapshot_id, rolling_bars)
        universes.store_market_caps(cap_id, now, "tushare_daily_basic", caps, now)
        universe_id = universes.create_snapshot(cap_id, trade_date, DynamicUniverseRule(
            args.group_name, minimum, args.momentum_days, args.top_n), MarketDataStore(connection).load_bars(snapshot_id), now)
    print(json.dumps({"market_snapshot_id": snapshot_id, "market_cap_snapshot_id": cap_id, "universe_snapshot_id": universe_id, "candidates": len(caps), "bars": len(rolling_bars), "incremental_dates": [str(day) for day in required_days]}))


if __name__ == "__main__":
    main()
