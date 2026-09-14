"""Create all configured monitoring-group research snapshots after market close.

The script is deliberately separate from production order generation.  It archives
the full-market daily facts, refreshes daily eligibility evidence, then freezes
research recommendations for every configured monitoring group.
"""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

from quant_core.database import read_connection, writer_connection
from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.models import DayBar
from quant_core.snapshots import SnapshotService, daily_market_close_timestamp, write_manifest
from quant_core.tushare_source import create_tushare_client
from quant_core.universe import UniverseService


ROOT = Path(__file__).resolve().parents[1]


def _is_a_share(ticker: str) -> bool:
    return ticker.endswith((".SH", ".SZ")) and not ticker.startswith(("200", "900"))


def _run(script: str, arguments: list[str]) -> dict:
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT,
                               text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"{script} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _snapshot_full_market(db_path: str, trade_date: date, artifact_dir: Path) -> tuple[str, str]:
    snapshot_id = f"tushare_monitoring_research_{trade_date:%Y%m%d}"
    cap_id = f"tushare_monitoring_research_cap_{trade_date:%Y%m%d}"
    with read_connection(db_path) as connection:
        exists = connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone()
    if exists:
        with read_connection(db_path) as connection:
            cap_exists = connection.execute("SELECT 1 FROM market_cap_snapshots WHERE market_cap_snapshot_id = ?", [cap_id]).fetchone()
        if not cap_exists:
            raise RuntimeError("existing monitoring market snapshot is missing its market-cap snapshot")
        return snapshot_id, cap_id
    load_env_file(ROOT / ".env")
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    client = create_tushare_client(token)
    value = trade_date.strftime("%Y%m%d")
    daily = client.daily(trade_date=value, fields="ts_code,trade_date,open,high,low,close,vol,amount").to_dict("records")
    basics = client.daily_basic(trade_date=value, fields="ts_code,trade_date,total_mv").to_dict("records")
    daily = [row for row in daily if _is_a_share(str(row.get("ts_code", "")))]
    basics = [row for row in basics if _is_a_share(str(row.get("ts_code", "")))]
    if not daily or not basics:
        raise RuntimeError("Tushare full-market daily or daily_basic response is empty")
    bars = [DayBar(trade_date, str(row["ts_code"]), Decimal(str(row["open"])), Decimal(str(row["high"])),
                   Decimal(str(row["low"])), Decimal(str(row["close"])), int(Decimal(str(row["vol"])) * 100),
                   Decimal(str(row["amount"])) * 1000, None, None)
            for row in daily]
    caps = {str(row["ts_code"]): Decimal(str(row["total_mv"])) * Decimal("10000") for row in basics
            if row.get("total_mv") not in (None, "")}
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"{snapshot_id}.raw.json"
    raw_path.write_text(json.dumps({"daily": daily, "daily_basic": basics}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    manifest_path = artifact_dir / f"{snapshot_id}.manifest.json"
    manifest_hash = write_manifest(manifest_path, {str(raw_path): sha256(raw_path.read_bytes()).hexdigest()})
    now = datetime.now(timezone.utc)
    published_at = daily_market_close_timestamp(trade_date)
    with writer_connection(db_path) as connection:
        SnapshotService(connection).register_market_snapshot(snapshot_id, trade_date, "tushare_monitoring_research_daily",
                                                              published_at, now, str(manifest_path), manifest_hash, now)
        MarketDataStore(connection).store_bars(snapshot_id, bars)
        UniverseService(connection).store_market_caps(cap_id, now, "tushare_monitoring_research_daily_basic", caps, now)
    return snapshot_id, cap_id


def _history_snapshot_ids(db_path: str, trade_date: date, required_days: int) -> list[str]:
    with read_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel = 'tushare_history_daily' "
            "AND trade_date < ? ORDER BY trade_date DESC LIMIT ?", [trade_date, required_days]
        ).fetchall()
    if len(rows) != required_days:
        raise RuntimeError(f"full-market history is incomplete: need {required_days} prior trading days, found {len(rows)}")
    return [row[0] for row in reversed(rows)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--effective-as-of", required=True)
    parser.add_argument("--groups-config", default="config/monitoring_groups.json")
    parser.add_argument("--artifact-dir", default="data/monitoring_daily")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    artifact_dir = Path(args.artifact_dir)
    market_snapshot_id, cap_id = _snapshot_full_market(args.db, trade_date, artifact_dir)
    listing = _run("backfill_security_listings.py", ["--db", args.db])
    st = _run("snapshot_tushare_current_st.py", ["--db", args.db, "--trade-date", trade_date.isoformat()])
    history = _history_snapshot_ids(args.db, trade_date, required_days=30)
    command = ["--db", args.db, "--market-cap-snapshot-id", cap_id, "--listing-snapshot-id", listing["listing_snapshot_id"],
               "--st-backfill-run-id", st["st_backfill_run_id"], "--as-of-date", trade_date.isoformat(),
               "--target-date", args.target_date, "--effective-as-of", args.effective_as_of,
               "--groups-config", args.groups_config]
    for snapshot_id in [*history, market_snapshot_id]:
        command.extend(("--market-snapshot-id", snapshot_id))
    groups = _run("run_monitoring_groups.py", command)
    print(json.dumps({"trade_date": trade_date.isoformat(), "market_snapshot_id": market_snapshot_id,
                      "market_cap_snapshot_id": cap_id, "listing_snapshot_id": listing["listing_snapshot_id"],
                      "st_backfill_run_id": st["st_backfill_run_id"], "groups": groups}, ensure_ascii=False))


if __name__ == "__main__":
    main()
