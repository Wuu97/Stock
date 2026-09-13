"""Derive full-market PIT capitalisation snapshots from archived Tushare facts."""

import argparse
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from quant_core.database import read_connection, writer_connection
from quant_core.universe import UniverseService


SOURCE_CHANNEL = "derived_tushare_history_daily_basic_v1"


def _is_mainland_a_share(ticker: str) -> bool:
    return ticker.endswith((".SH", ".SZ")) and not ticker.startswith(("200", "900"))


def _load_days(db_path: str, start: date, end: date) -> list[tuple[date, str, datetime]]:
    with read_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT trade_date, manifest_path, source_published_at FROM market_data_snapshots "
            "WHERE source_channel = 'tushare_history_daily' AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            [start, end],
        ).fetchall()
    return [(trade_date, str(manifest_path), published_at) for trade_date, manifest_path, published_at in rows]


def _cap_values(manifest_path: str) -> tuple[dict[str, Decimal], str, str]:
    raw_path = Path(manifest_path).with_suffix("").with_suffix(".raw.json")
    raw_bytes = raw_path.read_bytes()
    payload = json.loads(raw_bytes)
    rows = payload.get("daily_basic")
    if not isinstance(rows, list):
        raise RuntimeError(f"archived daily_basic is missing from {raw_path}")
    values = {
        str(row["ts_code"]): Decimal(str(row["total_mv"])) * Decimal("10000")
        for row in rows
        if _is_mainland_a_share(str(row.get("ts_code", ""))) and row.get("total_mv") not in (None, "")
    }
    if not values:
        raise RuntimeError(f"archived daily_basic has no mainland A-share market caps: {raw_path}")
    return values, str(raw_path), sha256(raw_bytes).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--report-path", default="data/ml_history/full_market_cap_backfill_report.json")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end or args.max_days is not None and args.max_days < 1:
        raise ValueError("invalid date range or max-days")

    created, skipped = [], []
    for trade_date, manifest_path, published_at in _load_days(args.db, start, end):
        snapshot_id = f"tushare_full_history_cap_{trade_date:%Y%m%d}"
        with writer_connection(args.db) as connection:
            exists = connection.execute(
                "SELECT 1 FROM market_cap_snapshots WHERE market_cap_snapshot_id = ?", [snapshot_id]
            ).fetchone()
            if exists:
                skipped.append(trade_date.isoformat())
                continue
            values, raw_path, raw_hash = _cap_values(manifest_path)
            UniverseService(connection).store_market_caps(snapshot_id, published_at, SOURCE_CHANNEL, values, published_at)
            created.append({"trade_date": trade_date.isoformat(), "snapshot_id": snapshot_id,
                            "ticker_count": len(values), "raw_artifact_path": raw_path,
                            "raw_artifact_sha256": raw_hash})
        if args.max_days is not None and len(created) >= args.max_days:
            break

    report = {"source_channel": SOURCE_CHANNEL, "created": created, "skipped_count": len(skipped),
              "requested_start_date": start.isoformat(), "requested_end_date": end.isoformat()}
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"created_count": len(created), "skipped_count": len(skipped), "report": str(report_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
