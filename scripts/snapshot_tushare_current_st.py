"""Archive the current A-share ST name status for one completed trading day.

This is a daily, point-in-time supplement to the historical BaoStock backfill.
It uses Tushare's current ``stock_basic`` names and deliberately records its own
source policy instead of pretending that it is historical BaoStock evidence.
"""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

from quant_core.database import writer_connection
from quant_core.environment import load_env_file
from quant_core.tushare_source import create_tushare_client


SOURCE_CHANNEL = "tushare_stock_basic_current_name_st_v1"
POLICY_VERSION = "tushare_current_name_contains_st_v1"


def _is_a_share(ticker: str) -> bool:
    return ticker.endswith((".SH", ".SZ")) and not ticker.startswith(("200", "900"))


def _is_st_name(name: object) -> bool:
    return "ST" in str(name or "").upper()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--artifact-dir", default="data/st_history/tushare_current")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")

    client = create_tushare_client(token)
    records = []
    for status in ("L", "D", "P"):
        records.extend(client.stock_basic(exchange="", list_status=status,
                                          fields="ts_code,name,list_status").to_dict("records"))
    records = [row for row in records if _is_a_share(str(row.get("ts_code", "")))]
    if not records:
        raise RuntimeError("Tushare stock_basic returned no mainland A-share names")
    now = datetime.now(timezone.utc)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
    raw_hash = sha256(raw_bytes).hexdigest()
    raw_path = artifact_dir / f"stock_basic_st_{trade_date:%Y%m%d}_{raw_hash[:20]}.raw.json"
    raw_path.write_bytes(raw_bytes)
    run_id = "tsst_" + uuid5(NAMESPACE_URL, f"{SOURCE_CHANNEL}:{POLICY_VERSION}:{trade_date}:{raw_hash}").hex
    rows = [(run_id, str(row["ts_code"]), trade_date, _is_st_name(row.get("name")), str(raw_path), raw_hash, now)
            for row in records]
    states = [(run_id, str(row["ts_code"]), "SUCCEEDED", 1, 1, str(raw_path), raw_hash, None, now)
              for row in records]
    with writer_connection(args.db) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO st_history_backfill_runs VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, SOURCE_CHANNEL, trade_date, trade_date, POLICY_VERSION, now],
        )
        connection.executemany("INSERT OR IGNORE INTO st_history_daily VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        connection.executemany("INSERT OR IGNORE INTO st_history_backfill_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", states)
    print(json.dumps({"st_backfill_run_id": run_id, "trade_date": trade_date.isoformat(),
                      "ticker_count": len(rows), "st_count": sum(row[3] for row in rows),
                      "raw_artifact": str(raw_path), "raw_sha256": raw_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
