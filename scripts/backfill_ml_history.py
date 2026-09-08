"""Backfill auditable A-share daily inputs for PIT ML research.

Each trading date is an atomic unit: raw provider responses are archived first,
then the raw bars, market-cap snapshot and historical universe are committed
together.  Existing complete dates are skipped on resume.
"""

import argparse
import csv
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
from quant_core.tushare_source import merge_daily_limits, row_to_daily_limit
from quant_core.universe import DynamicUniverseRule, UniverseService


def _bars(records: list[dict]) -> list[DayBar]:
    return [DayBar(
        datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(), str(row["ts_code"]),
        Decimal(str(row["open"])), Decimal(str(row["high"])), Decimal(str(row["low"])), Decimal(str(row["close"])),
        int(Decimal(str(row["vol"])) * Decimal("100")), Decimal(str(row["amount"])) * Decimal("1000"), None, None,
    ) for row in records]


def _is_mainland_a_share(ticker: str) -> bool:
    """Keep SSE/SZSE A shares; the current strategy does not cover BSE or B shares."""
    return ticker.endswith((".SH", ".SZ")) and not ticker.startswith(("200", "900"))


def _require_columns(frame, fields: set[str], endpoint: str) -> list[dict]:
    if not fields.issubset(frame.columns):
        raise RuntimeError(f"Tushare {endpoint} response is missing required fields")
    records = frame.to_dict("records")
    if not records:
        raise RuntimeError(f"Tushare {endpoint} response is empty for a trading day")
    return records


def _append_adjusted_rows(path: Path, daily_rows: list[dict], factor_rows: list[dict], written_dates: set[str]) -> None:
    trade_date = str(daily_rows[0]["trade_date"])
    if trade_date in written_dates:
        return
    factors = {str(row["ts_code"]): Decimal(str(row["adj_factor"])) for row in factor_rows}
    missing = sorted({str(row["ts_code"]) for row in daily_rows} - set(factors))
    if missing:
        raise RuntimeError(f"Tushare adj_factor is incomplete for {len(missing)} tickers")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=("trade_date", "ticker", "adj_close"))
        if write_header:
            writer.writeheader()
        writer.writerows({"trade_date": str(row["trade_date"]), "ticker": str(row["ts_code"]),
                          "adj_close": str(Decimal(str(row["close"])) * factors[str(row["ts_code"])])}
                         for row in daily_rows)
    written_dates.add(trade_date)


def _adjusted_dates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as source:
        return {row["trade_date"] for row in csv.DictReader(source)}


def _push_rolling(rolling_bars: list[DayBar], bars: list[DayBar], momentum_days: int) -> list[DayBar]:
    combined = rolling_bars + bars
    keep_dates = sorted({bar.trade_date for bar in combined})[-(momentum_days + 1):]
    return [bar for bar in combined if bar.trade_date in set(keep_dates)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--group-name", default="all_a_large_cap_momentum")
    parser.add_argument("--min-total-market-cap", default="80000000000")
    parser.add_argument("--momentum-days", type=int, default=30)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--artifact-dir", default="data/ml_history")
    parser.add_argument("--max-days", type=int, help="Safe bounded run for testing or incremental backfill")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end or args.max_days is not None and args.max_days < 1:
        raise ValueError("invalid date range or max-days")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    client = ts.pro_api(token)
    warmup_start = start - timedelta(days=120)
    calendar = client.trade_cal(exchange="SSE", start_date=warmup_start.strftime("%Y%m%d"),
                                end_date=end.strftime("%Y%m%d"), is_open="1")
    all_days = [datetime.strptime(str(value), "%Y%m%d").date() for value in sorted(calendar["cal_date"].tolist())]
    first_target = next((index for index, day in enumerate(all_days) if day >= start), None)
    if first_target is None:
        raise ValueError("no trading dates in requested range")
    days = all_days[max(0, first_target - args.momentum_days):]
    if args.max_days:
        days = days[:args.max_days]
    if not days:
        raise ValueError("no trading dates in requested range")
    rule = DynamicUniverseRule(args.group_name, Decimal(args.min_total_market_cap), args.momentum_days, args.top_n,
                               "historical_pit_cap_momentum_v1")
    artifact_dir, adjusted_path = Path(args.artifact_dir), Path(args.artifact_dir) / "adjusted_closes.csv"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(args.db)
    completed, written_dates = 0, _adjusted_dates(adjusted_path)
    try:
        market_data, snapshots, universes = MarketDataStore(connection), SnapshotService(connection), UniverseService(connection)
        rolling_bars: list[DayBar] = []
        for trade_date in days:
            snapshot_id, cap_id = f"tushare_history_{trade_date:%Y%m%d}", f"tushare_history_cap_{trade_date:%Y%m%d}"
            existing = connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone()
            if existing:
                rolling_bars = _push_rolling(rolling_bars, market_data.load_bars(snapshot_id), args.momentum_days)
                continue
            day_value = trade_date.strftime("%Y%m%d")
            daily_all = _require_columns(client.daily(trade_date=day_value, fields="ts_code,trade_date,open,high,low,close,vol,amount"),
                                         {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}, "daily")
            basic_all = _require_columns(client.daily_basic(trade_date=day_value, fields="ts_code,trade_date,total_mv"),
                                         {"ts_code", "trade_date", "total_mv"}, "daily_basic")
            factors = _require_columns(client.adj_factor(trade_date=day_value, fields="ts_code,trade_date,adj_factor"),
                                       {"ts_code", "trade_date", "adj_factor"}, "adj_factor")
            daily = [row for row in daily_all if _is_mainland_a_share(str(row["ts_code"]))]
            basic = [row for row in basic_all if _is_mainland_a_share(str(row["ts_code"]))]
            limit_records = _require_columns(client.stk_limit(trade_date=day_value),
                                             {"ts_code", "trade_date", "up_limit", "down_limit"}, "stk_limit")
            daily_tickers = {str(row["ts_code"]) for row in daily}
            limits = [row_to_daily_limit(row) for row in limit_records if str(row["ts_code"]) in daily_tickers]
            by_limit = {(item.trade_date, item.ticker): item for item in limits}
            raw_bars = _bars(daily)
            missing_limits = [bar.ticker for bar in raw_bars if (bar.trade_date, bar.ticker) not in by_limit]
            if missing_limits:
                raise RuntimeError(f"Tushare stk_limit is incomplete for {len(missing_limits)} tickers on {day_value}")
            bars = list(merge_daily_limits(raw_bars, limits))
            caps = {str(row["ts_code"]): Decimal(str(row["total_mv"])) * Decimal("10000") for row in basic
                    if Decimal(str(row["total_mv"])) * Decimal("10000") >= rule.min_total_market_cap}
            if not caps:
                raise RuntimeError(f"no qualifying market-cap values on {day_value}")
            raw_path = artifact_dir / f"{snapshot_id}.raw.json"
            raw_path.write_text(json.dumps({"daily": daily_all, "daily_basic": basic_all, "adj_factor": factors,
                                            "stk_limit": [item.__dict__ for item in limits]}, default=str,
                                           ensure_ascii=False, sort_keys=True), encoding="utf-8")
            manifest_path = artifact_dir / f"{snapshot_id}.manifest.json"
            manifest_hash = write_manifest(manifest_path, {str(raw_path): sha256(raw_path.read_bytes()).hexdigest()})
            now = datetime.now(timezone.utc)
            try:
                connection.execute("BEGIN")
                snapshots.register_market_snapshot(snapshot_id, trade_date, "tushare_history_daily", now, now,
                                                   str(manifest_path), manifest_hash, now)
                market_data.store_bars(snapshot_id, bars)
                universes.store_market_caps(cap_id, now, "tushare_history_daily_basic", caps, now)
                rolling_bars = _push_rolling(rolling_bars, bars, args.momentum_days)
                if len({bar.trade_date for bar in rolling_bars}) >= args.momentum_days + 1:
                    universes.create_snapshot(cap_id, trade_date, rule, rolling_bars, now)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            _append_adjusted_rows(adjusted_path, daily, factors, written_dates)
            completed += 1
    finally:
        connection.close()
    print(json.dumps({"completed_dates": completed, "adjusted_csv": str(adjusted_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
