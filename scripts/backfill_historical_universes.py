"""Build one immutable, fail-closed historical PIT large-cap momentum universe."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from quant_core.database import writer_connection

from quant_core.market_data import MarketDataStore
from quant_core.universe import DynamicUniverseRule, UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--listing-snapshot-id", required=True)
    parser.add_argument("--st-backfill-run-id", required=True)
    parser.add_argument("--calendar-reference", required=True,
                        help="Immutable JSON object with source, start_date, end_date and trading_days")
    parser.add_argument("--market-source", default="tushare_oos2020_history_daily_v1",
                        help="Frozen OOS market evidence source; Discovery sources are forbidden.")
    parser.add_argument("--market-snapshot-prefix", default="oos2020_tushare_market_",
                        help="Frozen OOS market snapshot-id prefix.")
    parser.add_argument("--cap-snapshot-prefix", default="oos2020_tushare_cap_",
                        help="Frozen OOS market-cap snapshot-id prefix.")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end:
        raise ValueError("start-date must not be after end-date")
    if args.market_source == "tushare_history_daily" or not args.market_source.startswith("tushare_oos"):
        raise ValueError("market-source must be an explicit frozen OOS evidence source, never Discovery")
    rule = DynamicUniverseRule(args.group_name, Decimal("80000000000"), 30, 50, "current_cap_momentum_v1",
                               min_listing_trading_days=60, require_non_st=True)
    connection = writer_connection(args.db, transaction=False)
    try:
        if connection.execute("SELECT 1 FROM universe_snapshots WHERE group_name = ? LIMIT 1", [args.group_name]).fetchone():
            raise ValueError("historical group already exists; choose a new immutable group name")
        listing = connection.execute(
            "SELECT source_channel FROM security_listing_snapshots WHERE listing_snapshot_id = ?", [args.listing_snapshot_id]
        ).fetchone()
        if not listing or listing[0] != "historical_listing_fact_reference_v1":
            raise ValueError("listing_snapshot_id must identify historical_listing_fact_reference_v1")
        st_run = connection.execute(
            "SELECT start_trade_date, end_trade_date, source_policy_version FROM st_history_backfill_runs WHERE backfill_run_id = ?",
            [args.st_backfill_run_id],
        ).fetchone()
        if not st_run or st_run[0] > start or st_run[1] < end or st_run[2] != "baostock_is_st_supplier_fact_v1":
            raise ValueError("st_backfill_run_id does not provide the required BaoStock historical PIT coverage")
        days = [row[0] for row in connection.execute(
            "SELECT DISTINCT trade_date FROM market_data_snapshots WHERE source_channel = ? "
            "AND trade_date BETWEEN ? AND ? ORDER BY trade_date", [args.market_source, start, end]
        ).fetchall()]
        if not days:
            raise ValueError("no historical trading days in requested interval")
        calendar_path = Path(args.calendar_reference)
        calendar_payload = json.loads(calendar_path.read_text(encoding="utf-8"))
        calendar_hash = sha256(json.dumps(calendar_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (calendar_payload.get("reference_type") != "HISTORICAL_TRADING_CALENDAR_REFERENCE"
                or calendar_payload.get("reference_version") != "historical_trading_calendar_reference_v1"
                or calendar_payload.get("source") != "tushare_trade_cal_sse_open_days_v1"
                or calendar_path.stem.rsplit("_", 1)[-1] != calendar_hash[:20]):
            raise ValueError("calendar reference must be a hash-addressed frozen Tushare SSE open-day reference")
        calendar_days = [date.fromisoformat(value) for value in calendar_payload.get("trading_days", [])]
        if not calendar_days or min(calendar_days) > start or max(calendar_days) < end:
            raise ValueError("calendar reference does not cover requested historical range")
        calendar_interval_days = {day for day in calendar_days if start <= day <= end}
        snapshot_interval_days = set(days)
        missing_snapshot_days = sorted(calendar_interval_days - snapshot_interval_days)
        unexpected_snapshot_days = sorted(snapshot_interval_days - calendar_interval_days)
        if missing_snapshot_days or unexpected_snapshot_days:
            raise ValueError(
                "historical market snapshot calendar mismatch; "
                f"missing_snapshot_days={[day.isoformat() for day in missing_snapshot_days]}; "
                f"unexpected_snapshot_days={[day.isoformat() for day in unexpected_snapshot_days]}"
            )
        service, store, created = UniverseService(connection), MarketDataStore(connection), []
        # A historical group is immutable.  Therefore a partially created group
        # is worse than no group: it prevents a safe retry.  Commit only after
        # every requested date has passed the evidence gates and been persisted.
        connection.execute("BEGIN TRANSACTION")
        try:
            for day in days:
                market_id = f"{args.market_snapshot_prefix}{day:%Y%m%d}"
                cap_id = f"{args.cap_snapshot_prefix}{day:%Y%m%d}"
                cap_source = connection.execute(
                    "SELECT source_channel FROM market_cap_snapshots WHERE market_cap_snapshot_id = ?", [cap_id]
                ).fetchone()
                if not cap_source:
                    raise RuntimeError(f"market cap snapshot is missing for {day}")
                if cap_source[0] != args.market_source:
                    raise RuntimeError(f"frozen OOS market-cap snapshot source is mismatched for {day}")
                source = connection.execute("SELECT source_channel FROM market_data_snapshots WHERE market_snapshot_id=?", [market_id]).fetchone()
                if not source or source[0] != args.market_source:
                    raise RuntimeError(f"frozen OOS market snapshot is missing or mismatched for {day}")
                source_ids = [row[0] for row in connection.execute(
                    "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel = ? "
                    "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 31", [args.market_source, day]
                ).fetchall()]
                if len(source_ids) < 31:
                    raise RuntimeError(f"insufficient trailing history for {day}")
                service.create_snapshot(cap_id, day, rule, store.load_bars_many(reversed(source_ids)), datetime.now(timezone.utc),
                                        listing_snapshot_id=args.listing_snapshot_id,
                                        st_backfill_run_id=args.st_backfill_run_id,
                                        trading_days=calendar_days)
                created.append(day.isoformat())
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()
    print({"created": created, "count": len(created), "calendar_reference": args.calendar_reference})


if __name__ == "__main__":
    main()
