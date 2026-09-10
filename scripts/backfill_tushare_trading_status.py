"""Archive official Tushare suspension/resumption facts by precise request or full-market page."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import duckdb

from quant_core.environment import load_env_file
from quant_core.trading_status import TradingStatusStore
from quant_core.tushare_source import create_tushare_client, fetch_trading_status_rows, iter_trading_status_pages, row_to_trading_status_event


SOURCE_CHANNEL = "tushare_suspend_d"


def main() -> None:
    args = build_parser().parse_args()
    if (args.ticker or args.all_market) and (not args.start_date or not args.end_date):
        raise ValueError("--ticker and --all-market require --start-date and --end-date")
    start = date.fromisoformat(args.start_date) if args.start_date else None
    end = date.fromisoformat(args.end_date) if args.end_date else None
    if start and end and (start > end or args.page_size <= 0):
        raise ValueError("invalid date range or page size")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = create_tushare_client(token)
    connection = duckdb.connect(args.db)
    try:
        store = TradingStatusStore(connection)
        result = []
        if args.all_market:
            requests = ((f"all_{offset:06d}", start, end, rows) for offset, rows in iter_trading_status_pages(client, start, end, args.page_size))
        else:
            requests = ((label, request_start, request_end, fetch_trading_status_rows(client, request_start, request_end, ticker))
                        for label, request_start, request_end, ticker in _requests(args.ticker, args.trade_date, start, end))
        for label, request_start, request_end, rows in requests:
            result.append(_archive_page(connection, store, output_dir, label, request_start, request_end, rows))
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    request = parser.add_mutually_exclusive_group(required=True)
    request.add_argument("--ticker", action="append")
    request.add_argument("--trade-date", action="append")
    request.add_argument("--all-market", action="store_true")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--output-dir", default="data/trading_status")
    return parser


def _archive_page(connection, store, output_dir, label, start, end, rows):
    raw_path = output_dir / f"{SOURCE_CHANNEL}_{label}_{start:%Y%m%d}_{end:%Y%m%d}.raw.json"
    raw_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    snapshot_id = f"{SOURCE_CHANNEL}_{raw_hash[:24]}"
    if connection.execute("SELECT 1 FROM trading_status_snapshots WHERE trading_status_snapshot_id = ?", [snapshot_id]).fetchone() is None:
        now = datetime.now(timezone.utc)
        store.store_snapshot(snapshot_id, SOURCE_CHANNEL, start, end, str(raw_path), raw_hash, now,
                             tuple(row_to_trading_status_event(row) for row in rows), now)
    return {"request": label, "snapshot_id": snapshot_id, "events": len(rows), "raw_artifact": str(raw_path)}


def _parse_trade_date(value: str) -> date:
    return date.fromisoformat(value if "-" in value else f"{value[:4]}-{value[4:6]}-{value[6:]}")


def _requests(tickers, trade_dates, start, end):
    if tickers:
        return [(ticker.replace(".", "_"), start, end, ticker) for ticker in sorted(set(tickers))]
    dates = sorted({_parse_trade_date(value) for value in trade_dates})
    return [("all", trade_date, trade_date, None) for trade_date in dates]


if __name__ == "__main__":
    main()
