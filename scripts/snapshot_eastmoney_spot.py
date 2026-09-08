"""Freeze a low-frequency Eastmoney spot snapshot for monitored A-share tickers."""

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

import duckdb

from quant_core.eastmoney_source import fetch_spot_payload, parse_spot_quote, quote_to_bar
from quant_core.market_data import MarketDataStore
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--tickers", required=True, help="Comma-separated monitored A-share tickers")
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--artifact-dir", default="data/spot_snapshots")
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.trade_date)
    snapshot_id = args.snapshot_id or str(uuid4())
    raw_payloads = {ticker: fetch_spot_payload(ticker) for ticker in args.tickers.split(",")}
    quotes = [parse_spot_quote(ticker, payload) for ticker, payload in raw_payloads.items()]
    artifact_path = Path(args.artifact_dir) / f"eastmoney_{snapshot_id}.json"
    raw_path = Path(args.artifact_dir) / f"eastmoney_{snapshot_id}.raw.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps([asdict(quote) for quote in quotes], default=str, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    raw_path.write_text(json.dumps(raw_payloads, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    artifact_hash = sha256(artifact_path.read_bytes()).hexdigest()
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    manifest_path = Path(args.artifact_dir) / f"market_{snapshot_id}.manifest.json"
    manifest_hash = write_manifest(manifest_path, {str(artifact_path): artifact_hash, str(raw_path): raw_hash})
    now = datetime.now(timezone.utc)
    connection = duckdb.connect(args.db)
    SnapshotService(connection).register_market_snapshot(
        snapshot_id, trade_date, "eastmoney_spot", now, now, str(manifest_path), manifest_hash, now
    )
    MarketDataStore(connection).store_bars(snapshot_id, [quote_to_bar(quote, trade_date) for quote in quotes])
    UniverseService(connection).store_market_caps(
        f"{snapshot_id}_market_cap", now, "eastmoney_spot", {quote.ticker: quote.total_market_cap for quote in quotes}, now
    )
    connection.close()
    print(snapshot_id)


if __name__ == "__main__":
    main()
