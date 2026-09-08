"""Store a reproducible, immutable Tushare daily-bar snapshot for selected tickers."""

import argparse
from datetime import date, datetime, timezone
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
from quant_core.universe import UniverseService


def _trading_dates(client, start: str, end: str) -> list[str]:
    calendar = client.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    return sorted(str(value) for value in calendar["cal_date"].tolist())


def _bars_from_records(records: list[dict]) -> list[DayBar]:
    return [DayBar(
        trade_date=datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(),
        ticker=str(row["ts_code"]),
        open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
        volume=int(Decimal(str(row["vol"])) * Decimal("100")),
        amount=Decimal(str(row["amount"])) * Decimal("1000"),
        limit_up=None, limit_down=None, status="TRADING",
    ) for row in records]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--universe-snapshot-id", required=True)
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--artifact-dir", default="data/tushare_snapshots")
    args = parser.parse_args()

    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end:
        raise ValueError("--start-date must not be after --end-date")

    connection = duckdb.connect(args.db)
    try:
        tickers = sorted(UniverseService(connection).member_tickers(args.universe_snapshot_id))
        if not tickers:
            raise ValueError("universe snapshot has no members")
        client = ts.pro_api(token)
        records = []
        for trading_date in _trading_dates(client, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")):
            frame = client.daily(trade_date=trading_date,
                                 fields="ts_code,trade_date,open,high,low,close,vol,amount")
            records.extend(row for row in frame.to_dict("records") if str(row["ts_code"]) in tickers)
        if not records:
            raise RuntimeError("Tushare daily returned no bars for the selected universe")

        artifact_dir = Path(args.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        raw_path = artifact_dir / f"tushare_daily_{args.snapshot_id}.raw.json"
        raw_path.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
        raw_hash = sha256(raw_path.read_bytes()).hexdigest()
        manifest_path = artifact_dir / f"market_{args.snapshot_id}.manifest.json"
        manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
        now = datetime.now(timezone.utc)
        SnapshotService(connection).register_market_snapshot(
            args.snapshot_id, end, "tushare_daily", now, now,
            str(manifest_path), manifest_hash, now,
        )
        MarketDataStore(connection).store_bars(args.snapshot_id, _bars_from_records(records))
    finally:
        connection.close()
    print(args.snapshot_id, len(records), len(tickers))


if __name__ == "__main__":
    main()
