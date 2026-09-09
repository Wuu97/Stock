"""Archive official Tushare suspension/resumption facts by security or trade date."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import duckdb
import tushare as ts

from quant_core.environment import load_env_file
from quant_core.trading_status import TradingStatusEvent, TradingStatusStore


SOURCE_CHANNEL = "tushare_suspend_d"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    request = parser.add_mutually_exclusive_group(required=True)
    request.add_argument("--ticker", action="append")
    request.add_argument("--trade-date", action="append")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--output-dir", default="data/trading_status")
    args = parser.parse_args()
    if args.ticker and (not args.start_date or not args.end_date):
        raise ValueError("--ticker requires --start-date and --end-date")
    start = date.fromisoformat(args.start_date) if args.start_date else None
    end = date.fromisoformat(args.end_date) if args.end_date else None
    if start and end and start > end:
        raise ValueError("--start-date must not be after --end-date")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = ts.pro_api(token)
    connection = duckdb.connect(args.db)
    try:
        store = TradingStatusStore(connection)
        result = []
        requests = _requests(args.ticker, args.trade_date, start, end)
        for label, request_start, request_end, query in requests:
            frame = client.suspend_d(fields="ts_code,trade_date,suspend_timing,suspend_type", **query)
            rows = frame.to_dict("records")
            raw_path = output_dir / f"{SOURCE_CHANNEL}_{label}_{request_start:%Y%m%d}_{request_end:%Y%m%d}.raw.json"
            raw_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
            raw_hash = sha256(raw_path.read_bytes()).hexdigest()
            snapshot_id = f"{SOURCE_CHANNEL}_{raw_hash[:24]}"
            exists = connection.execute(
                "SELECT 1 FROM trading_status_snapshots WHERE trading_status_snapshot_id = ?", [snapshot_id]
            ).fetchone()
            if not exists:
                events = tuple(_event(row) for row in rows)
                now = datetime.now(timezone.utc)
                store.store_snapshot(snapshot_id, SOURCE_CHANNEL, request_start, request_end, str(raw_path), raw_hash, now, events, now)
            result.append({"request": label, "snapshot_id": snapshot_id, "events": len(rows), "raw_artifact": str(raw_path)})
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _event(row: dict) -> TradingStatusEvent:
    status = {"S": "SUSPENDED", "R": "RESUMED"}.get(str(row.get("suspend_type", "")))
    if status is None:
        raise ValueError("Tushare suspend_d returned an unknown suspend_type")
    return TradingStatusEvent(
        ticker=str(row["ts_code"]), trade_date=_parse_trade_date(str(row["trade_date"])), status_code=status,
        suspend_timing=row.get("suspend_timing") or None,
    )


def _parse_trade_date(value: str) -> date:
    return date.fromisoformat(value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:]}")


def _requests(tickers, trade_dates, start, end):
    if tickers:
        return [(ticker.replace(".", "_"), start, end, {
            "ts_code": ticker, "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
        }) for ticker in sorted(set(tickers))]
    dates = sorted({_parse_trade_date(value) for value in trade_dates})
    return [("all", trade_date, trade_date, {"trade_date": trade_date.strftime("%Y%m%d")}) for trade_date in dates]


if __name__ == "__main__":
    main()
