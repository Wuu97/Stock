"""Archive paginated, full-market Tushare suspension/resumption facts."""

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
FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--output-dir", default="data/trading_status")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end or args.page_size <= 0:
        raise ValueError("invalid date range or page size")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = ts.pro_api(token)
    connection = duckdb.connect(args.db)
    try:
        store, result, offset = TradingStatusStore(connection), [], 0
        while True:
            frame = client.query(
                "suspend_d", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
                fields=FIELDS, offset=offset, limit=args.page_size,
            )
            rows = frame.to_dict("records")
            if not rows:
                break
            raw_path = output_dir / f"{SOURCE_CHANNEL}_all_{start:%Y%m%d}_{end:%Y%m%d}_{offset:06d}.raw.json"
            raw_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
            raw_hash = sha256(raw_path.read_bytes()).hexdigest()
            snapshot_id = f"{SOURCE_CHANNEL}_{raw_hash[:24]}"
            if connection.execute("SELECT 1 FROM trading_status_snapshots WHERE trading_status_snapshot_id = ?", [snapshot_id]).fetchone() is None:
                now = datetime.now(timezone.utc)
                store.store_snapshot(snapshot_id, SOURCE_CHANNEL, start, end, str(raw_path), raw_hash, now,
                                     tuple(_event(row) for row in rows), now)
            result.append({"offset": offset, "events": len(rows), "snapshot_id": snapshot_id, "raw_artifact": str(raw_path)})
            if len(rows) < args.page_size:
                break
            offset += args.page_size
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def _event(row: dict) -> TradingStatusEvent:
    status = {"S": "SUSPENDED", "R": "RESUMED"}.get(str(row.get("suspend_type", "")))
    if status is None:
        raise ValueError("Tushare suspend_d returned an unknown suspend_type")
    trade_date = str(row["trade_date"])
    return TradingStatusEvent(str(row["ts_code"]), date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"),
                              status, row.get("suspend_timing") or None)


if __name__ == "__main__":
    main()
