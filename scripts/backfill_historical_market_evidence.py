"""Backfill immutable historical market evidence without creating a Universe.

This is Phase 2 of the historical OOS workflow.  It consumes a previously
frozen calendar reference, archives each day's exact provider payload, then
persists only immutable market/cap facts.  Universe construction is deliberately
outside this command.
"""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

import tushare as ts

from quant_core.database import writer_connection
from quant_core.environment import load_env_file
from quant_core.historical_market_evidence import (BASIC_FIELDS, DAILY_FIELDS, FACTOR_FIELDS, LIMIT_FIELDS,
                                                    canonical_daily_evidence, canonical_evidence_bytes, require_records)
from quant_core.market_data import MarketDataStore
from quant_core.snapshots import SnapshotService, daily_market_close_timestamp, write_manifest
from quant_core.universe import UniverseService


DEFAULT_SOURCE = "tushare_oos2020_history_daily_v1"


def _calendar_days(path: Path, start: date, end: date) -> list[date]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("reference_type") != "HISTORICAL_TRADING_CALENDAR_REFERENCE":
        raise ValueError("calendar reference has an unexpected type")
    actual_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if path.stem.rsplit("_", 1)[-1] != actual_hash[:20]:
        raise ValueError("calendar reference filename hash does not match payload")
    days = [date.fromisoformat(value) for value in payload.get("trading_days", [])]
    selected = [day for day in days if start <= day <= end]
    if not selected:
        raise ValueError("frozen calendar has no requested trading days")
    return selected


def _existing_snapshot(connection, source: str, trade_date: date):
    rows = connection.execute(
        "SELECT market_snapshot_id, manifest_path, manifest_sha256 FROM market_data_snapshots "
        "WHERE source_channel=? AND trade_date=? ORDER BY market_snapshot_id", [source, trade_date],
    ).fetchall()
    if len(rows) > 1:
        raise RuntimeError(f"EVIDENCE_CONFLICT: multiple frozen snapshots exist for {source} {trade_date}")
    return rows[0] if rows else None


def _existing_evidence_hash(manifest_path: str, manifest_sha256: str) -> str:
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("manifest_sha256") != manifest_sha256:
        raise RuntimeError("stored market snapshot manifest hash mismatch")
    artifacts = manifest.get("artifacts", {})
    if len(artifacts) != 1:
        raise RuntimeError("stored market snapshot manifest must contain exactly one raw evidence artifact")
    raw_path, raw_hash = next(iter(artifacts.items()))
    raw = Path(raw_path).read_bytes()
    if sha256(raw).hexdigest() != raw_hash:
        raise RuntimeError("stored raw evidence artifact hash mismatch")
    return raw_hash


def _fetch(client, trade_date: date):
    day = trade_date.strftime("%Y%m%d")
    return (
        require_records(client.daily(trade_date=day, fields="ts_code,trade_date,open,high,low,close,vol,amount"), DAILY_FIELDS, "daily"),
        require_records(client.daily_basic(trade_date=day, fields="ts_code,trade_date,total_mv"), BASIC_FIELDS, "daily_basic"),
        require_records(client.adj_factor(trade_date=day, fields="ts_code,trade_date,adj_factor"), FACTOR_FIELDS, "adj_factor"),
        require_records(client.stk_limit(trade_date=day), LIMIT_FIELDS, "stk_limit"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--calendar-reference", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--artifact-dir", default="data/historical_evidence")
    parser.add_argument("--source", default=DEFAULT_SOURCE,
                        help="Immutable OOS source version, for example tushare_oos2020_history_daily_v2.")
    parser.add_argument("--market-snapshot-prefix", default="oos2020_tushare_market_")
    parser.add_argument("--cap-snapshot-prefix", default="oos2020_tushare_cap_")
    parser.add_argument("--minimum-execution-market-cap", default="80000000000",
                        help="Only the current high-cap execution candidate domain requires explicit price-limit facts.")
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--revalidate-existing", action="store_true",
                        help="Fetch existing request identities solely to detect provider revisions; never overwrites evidence.")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end or args.max_days is not None and args.max_days < 1:
        raise ValueError("invalid date range or max-days")
    if not args.source.startswith("tushare_oos") or args.source == "tushare_history_daily":
        raise ValueError("source must be a dedicated OOS evidence source")
    days = _calendar_days(Path(args.calendar_reference), start, end)
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    client, directory = ts.pro_api(token), Path(args.artifact_dir)
    directory.mkdir(parents=True, exist_ok=True)
    connection = writer_connection(args.db, transaction=False)
    completed = skipped = 0
    prior_execution_tickers = set()
    try:
        snapshots, market_data, universes = SnapshotService(connection), MarketDataStore(connection), UniverseService(connection)
        for trade_date in days:
            existing = _existing_snapshot(connection, args.source, trade_date)
            if existing and not args.revalidate_existing:
                _existing_evidence_hash(str(existing[1]), str(existing[2]))
                cap_id = f"{args.cap_snapshot_prefix}{trade_date:%Y%m%d}"
                prior_execution_tickers.update(row[0] for row in connection.execute(
                    "SELECT ticker FROM market_cap_values WHERE market_cap_snapshot_id=? AND total_market_cap>=?",
                    [cap_id, Decimal(args.minimum_execution_market_cap)],
                ).fetchall())
                skipped += 1
                continue
            if args.max_days is not None and completed >= args.max_days:
                break
            daily, basic, factors, limits = _fetch(client, trade_date)
            bars, caps, evidence = canonical_daily_evidence(
                trade_date, daily, basic, factors, limits, Decimal(args.minimum_execution_market_cap), prior_execution_tickers,
            )
            raw_bytes = canonical_evidence_bytes(evidence)
            raw_hash = sha256(raw_bytes).hexdigest()
            if existing:
                old_hash = _existing_evidence_hash(str(existing[1]), str(existing[2]))
                if old_hash == raw_hash:
                    skipped += 1
                    continue
                conflict = directory / "conflicts" / f"{args.source}_{trade_date:%Y%m%d}_{raw_hash[:20]}.raw.json"
                conflict.parent.mkdir(parents=True, exist_ok=True)
                if not conflict.exists():
                    conflict.write_bytes(raw_bytes)
                raise RuntimeError(f"EVIDENCE_CONFLICT: provider response changed for {args.source} {trade_date}; candidate preserved at {conflict}")
            snapshot_id, cap_id = f"{args.market_snapshot_prefix}{trade_date:%Y%m%d}", f"{args.cap_snapshot_prefix}{trade_date:%Y%m%d}"
            raw_path = directory / f"{snapshot_id}.raw.json"
            if raw_path.exists() and sha256(raw_path.read_bytes()).hexdigest() != raw_hash:
                raise RuntimeError(f"EVIDENCE_CONFLICT: unregistered raw artifact already differs for {trade_date}")
            raw_path.write_bytes(raw_bytes)
            manifest_path = directory / f"{snapshot_id}.manifest.json"
            manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
            now = datetime.now(timezone.utc)
            connection.execute("BEGIN")
            try:
                snapshots.register_market_snapshot(snapshot_id, trade_date, args.source, daily_market_close_timestamp(trade_date), now,
                                                   str(manifest_path), manifest_hash, now)
                market_data.store_bars(snapshot_id, bars)
                universes.store_market_caps(cap_id, now, args.source, caps, now)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            completed += 1
            prior_execution_tickers.update(ticker for ticker, cap in caps.items()
                                           if cap >= Decimal(args.minimum_execution_market_cap))
    finally:
        connection.close()
    print(json.dumps({"source": args.source, "completed_dates": completed, "skipped_dates": skipped,
                      "universe_created": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
