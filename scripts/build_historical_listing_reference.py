"""Create one immutable listing-date reference for historical eligibility reconstruction.

This is deliberately not labelled an as-of historical listing snapshot. It records
the later archival time and every supplier fact used to reconstruct stable list-date
eligibility, while unknown securities fail closed.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from quant_core.baostock_st_source import baostock_code, session
from quant_core.database import read_connection, writer_connection
from quant_core.historical_listing_reference import baostock_lifecycle_row, unresolved_tickers
from quant_core.security_eligibility import SecurityEligibilityStore, listing_rows


def _required_tickers(connection, group_name: str, start_date: str, end_date: str) -> list[str]:
    return [row[0] for row in connection.execute("""
        SELECT DISTINCT v.ticker FROM universe_snapshots u
        JOIN market_cap_values v ON v.market_cap_snapshot_id=u.market_cap_snapshot_id
        WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ?
          AND v.total_market_cap >= 80000000000 ORDER BY 1
    """, [group_name, start_date, end_date]).fetchall()]


def _base_rows(connection, snapshot_id: str) -> list[dict]:
    return [{"ts_code": ticker, "list_date": str(listed).replace("-", ""),
             "delist_date": None if delisted is None else str(delisted).replace("-", ""), "list_status": status}
            for ticker, listed, delisted, status in connection.execute(
                "SELECT ticker,list_date,delist_date,list_status FROM security_listing_values WHERE listing_snapshot_id=?", [snapshot_id]
            ).fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--source-listing-snapshot-id", required=True)
    parser.add_argument("--universe-group-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--artifact-dir", default="data/security_eligibility")
    args = parser.parse_args()
    with read_connection(args.db) as connection:
        required = _required_tickers(connection, args.universe_group_name, args.start_date, args.end_date)
        base = _base_rows(connection, args.source_listing_snapshot_id)
    by_ticker = {row["ts_code"]: row for row in base}
    missing = unresolved_tickers(required, by_ticker)
    fallback = {}
    if missing:
        with session() as client:
            for ticker in missing:
                response = client.query_stock_basic(code=baostock_code(ticker))
                rows = []
                while response.next():
                    rows.append(dict(zip(response.fields, response.get_row_data())))
                if len(rows) == 1:
                    fallback[ticker] = baostock_lifecycle_row(ticker, rows[0])
                    by_ticker[ticker] = fallback[ticker]
    unresolved = unresolved_tickers(required, by_ticker)
    if unresolved:
        raise ValueError("historical listing reference remains unresolved; fail closed: " + ", ".join(unresolved))
    selected = [by_ticker[ticker] for ticker in required]
    now = datetime.now(timezone.utc)
    payload = {"reference_type": "HISTORICAL_LISTING_FACT_REFERENCE", "reference_version": "historical_listing_fact_reference_v1",
               "eligibility_scope": {"universe_group_name": args.universe_group_name, "start_date": args.start_date, "end_date": args.end_date,
                                     "usage": "stable_list_date_plus_historical_trading_calendar_only"},
               "source_listing_snapshot_id": args.source_listing_snapshot_id, "base_rows": selected,
               "baostock_lifecycle_fallback": fallback, "archived_at": now.isoformat()}
    artifact_dir = Path(args.artifact_dir); artifact_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = sha256(raw.encode()).hexdigest()
    path = artifact_dir / f"historical_listing_reference_{digest[:20]}.raw.json"
    path.write_text(raw + "\n", encoding="utf-8")
    snapshot_id = f"historical_listing_reference_{digest[:20]}"
    with writer_connection(args.db) as connection:
        SecurityEligibilityStore(connection).store_listing_snapshot(snapshot_id, "historical_listing_fact_reference_v1",
            str(path), digest, now, listing_rows(selected), now)
    print(json.dumps({"listing_snapshot_id": snapshot_id, "reference_sha256": digest, "ticker_count": len(selected),
                      "baostock_fallback_tickers": sorted(fallback), "unresolved_tickers": unresolved}, ensure_ascii=False))


if __name__ == "__main__":
    main()
