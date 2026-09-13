"""Create one strict daily market snapshot for all simulated-account holdings."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

from quant_core.database import read_connection, writer_connection
from quant_core.eastmoney_source import fetch_history_payload, parse_history_bars
from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.models import DayBar
from quant_core.snapshots import SnapshotService, write_manifest
from quant_core.tushare_source import (create_tushare_client, fetch_daily_limit_records,
                                       fetch_etf_limit_records, merge_daily_limits, row_to_daily_limit)


def _held_tickers(connection):
    rows = connection.execute(
        "WITH disposed AS (SELECT lot_id, SUM(shares_deducted) AS shares FROM sim_lot_disposal_events GROUP BY lot_id) "
        "SELECT l.ticker, COALESCE(s.instrument_type, 'A_SHARE') "
        "FROM sim_position_lots l LEFT JOIN security_master s ON s.ticker = l.ticker "
        "LEFT JOIN disposed d ON d.lot_id = l.lot_id "
        "GROUP BY l.ticker, s.instrument_type HAVING SUM(l.orig_shares - COALESCE(d.shares, 0)) > 0 "
        "ORDER BY l.ticker"
    ).fetchall()
    if not rows:
        raise ValueError("there are no active simulated holdings")
    return rows


def _stock_bars(records, wanted, trade_date):
    """Normalize a full-market Tushare daily response for the held stock subset."""
    bars = []
    for row in records:
        ticker = str(row["ts_code"])
        if ticker not in wanted:
            continue
        bars.append(DayBar(trade_date, ticker, Decimal(str(row["open"])), Decimal(str(row["high"])),
                           Decimal(str(row["low"])), Decimal(str(row["close"])),
                           int(Decimal(str(row["vol"])) * Decimal("100")),
                           Decimal(str(row["amount"])) * Decimal("1000"), None, None, "TRADING"))
    return tuple(bars)


def _existing_bars(connection, tickers, trade_date):
    """Reuse already-audited same-day ETF bars before asking the vendor again."""
    if not tickers:
        return ()
    placeholders = ",".join("?" for _ in tickers)
    rows = connection.execute(
        "SELECT trade_date, ticker, open, high, low, close, volume, amount, limit_up, limit_down, status "
        "FROM daily_bars WHERE trade_date = ? AND ticker IN (" + placeholders + ") "
        "AND limit_up IS NOT NULL AND limit_down IS NOT NULL ORDER BY market_snapshot_id",
        [trade_date, *tickers],
    ).fetchall()
    by_ticker = {}
    for row in rows:
        by_ticker.setdefault(row[1], DayBar(*row))
    return tuple(by_ticker[ticker] for ticker in tickers if ticker in by_ticker)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date", required=True, help="Completed SSE trading date, YYYY-MM-DD.")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--artifact-dir", default="data/portfolio_market_snapshots")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    snapshot_id = args.snapshot_id or f"portfolio_market_{trade_date:%Y%m%d}"
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")

    with read_connection(args.db) as connection:
        if connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone():
            raise ValueError(f"snapshot already exists: {snapshot_id}")
        holdings = _held_tickers(connection)
        tickers = [ticker for ticker, _ in holdings]
        etfs = [ticker for ticker, kind in holdings if kind == "ETF"]
        stocks = [ticker for ticker, kind in holdings if kind != "ETF"]
        existing_etf_bars = _existing_bars(connection, etfs, trade_date)
    existing_etfs = {bar.ticker for bar in existing_etf_bars}
    missing_etfs = [ticker for ticker in etfs if ticker not in existing_etfs]
    raw_history = {ticker: fetch_history_payload(ticker, trade_date, trade_date) for ticker in missing_etfs}
    etf_bars = existing_etf_bars + tuple(
        bar for ticker in missing_etfs for bar in parse_history_bars(ticker, raw_history[ticker])
    )
    client = create_tushare_client(token)
    raw_stock_daily = client.daily(trade_date=trade_date.strftime("%Y%m%d"),
                                   fields="ts_code,trade_date,open,high,low,close,vol,amount").to_dict("records") if stocks else ()
    stock_bars = _stock_bars(raw_stock_daily, set(stocks), trade_date)
    bars = stock_bars + etf_bars
    returned_tickers = {bar.ticker for bar in bars}
    missing_bars = sorted(set(tickers) - returned_tickers)
    if missing_bars:
        raise RuntimeError(f"Eastmoney has no bar for active holdings: {', '.join(missing_bars)}")
    raw_stock_limits = fetch_daily_limit_records(token, [trade_date]) if stocks else ()
    raw_etf_limits = fetch_etf_limit_records(token, missing_etfs, trade_date, trade_date) if missing_etfs else ()
    wanted = set(tickers)
    raw_limits = tuple(row for row in (*raw_stock_limits, *raw_etf_limits) if str(row["ts_code"]) in wanted)
    enriched = merge_daily_limits(bars, (row_to_daily_limit(row) for row in raw_limits))
    missing_limits = [(bar.ticker, bar.trade_date) for bar in enriched if bar.limit_up is None or bar.limit_down is None]
    if missing_limits:
        raise RuntimeError(f"authoritative limits are missing for {len(missing_limits)} holdings: {missing_limits[:5]}")
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"portfolio_market_{trade_date:%Y%m%d}.raw.json"
    raw_path.write_text(json.dumps({"eastmoney_etf": raw_history, "tushare_daily": raw_stock_daily,
                                    "tushare_stk_limit": raw_stock_limits, "tushare_etf_limit": raw_etf_limits}, ensure_ascii=False, sort_keys=True,
                                   default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    manifest_path = artifact_dir / f"market_{snapshot_id}.manifest.json"
    manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
    now = datetime.now(timezone.utc)
    with writer_connection(args.db) as connection:
        if connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone():
            raise ValueError(f"snapshot already exists: {snapshot_id}")
        SnapshotService(connection).register_market_snapshot(snapshot_id, trade_date,
            "portfolio_eastmoney_history_with_tushare_limits", now, now, str(manifest_path), manifest_hash, now)
        MarketDataStore(connection).store_bars(snapshot_id, enriched)
    print(json.dumps({"snapshot_id": snapshot_id, "trade_date": str(trade_date), "tickers": tickers,
                      "bars": len(enriched)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
