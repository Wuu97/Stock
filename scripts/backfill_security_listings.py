"""Archive all Tushare stock listing facts for PIT new-list eligibility."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.environment import load_env_file
from quant_core.security_eligibility import SecurityEligibilityStore, listing_rows
from quant_core.tushare_source import create_tushare_client


def _is_supported_a_share(ticker: str) -> bool:
    return ticker.endswith((".SH", ".SZ")) and not ticker.startswith(("200", "900"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--artifact-dir", default="data/security_eligibility")
    args = parser.parse_args()
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")

    client = create_tushare_client(token)
    records = []
    for status in ("L", "D", "P"):
        records.extend(client.stock_basic(
            exchange="", list_status=status, fields="ts_code,name,list_date,delist_date,list_status"
        ).to_dict("records"))
    records = [row for row in records if _is_supported_a_share(str(row.get("ts_code", "")))]
    rows = listing_rows(records)
    now = datetime.now(timezone.utc)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    raw_path = artifact_dir / f"tushare_stock_basic_listings_{now:%Y%m%dT%H%M%SZ}.raw.json"
    raw_path.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    snapshot_id = f"tushare_stock_basic_listings_{raw_hash[:20]}"
    with writer_connection(args.db) as connection:
        exists = connection.execute(
            "SELECT 1 FROM security_listing_snapshots WHERE listing_snapshot_id = ?", [snapshot_id]
        ).fetchone()
        count = 0 if exists else SecurityEligibilityStore(connection).store_listing_snapshot(
            snapshot_id, "tushare_stock_basic_all_status_v1", str(raw_path), raw_hash, now, rows, now,
        )
    print(json.dumps({"listing_snapshot_id": snapshot_id, "stored_rows": count,
                      "raw_artifact": str(raw_path), "raw_sha256": raw_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
