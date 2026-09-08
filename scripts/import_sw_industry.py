"""Import an immutable SW2021 L3 taxonomy and current constituent memberships from Tushare."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from time import sleep

import duckdb
import tushare as ts

from quant_core.environment import load_env_file
from quant_core.industry_taxonomy import membership_rows, store_taxonomy_version, taxonomy_rows
from quant_core.snapshots import write_manifest
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--taxonomy-version", default=None)
    parser.add_argument("--universe-snapshot-id", default=None,
                        help="Import only the immutable PIT universe members; avoids full-market API throttling.")
    parser.add_argument("--artifact-dir", default="data/industry_taxonomy")
    parser.add_argument("--request-interval-seconds", type=float, default=0.36)
    args = parser.parse_args()
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    if args.request_interval_seconds < 0.31:
        raise ValueError("--request-interval-seconds must stay below Tushare's 200 calls/minute limit")
    version = args.taxonomy_version or f"SW2021_TUSHARE_{datetime.now(timezone.utc):%Y%m%d}"
    client = ts.pro_api(token)
    classifications = client.index_classify(level="L3", src="SW2021").to_dict("records")
    taxonomy = taxonomy_rows(classifications)
    code_by_index = {str(row["index_code"]): str(row["industry_code"]) for row in classifications}
    connection = duckdb.connect(args.db)
    try:
        tickers = sorted(UniverseService(connection).member_tickers(args.universe_snapshot_id)) if args.universe_snapshot_id else ()
        if args.universe_snapshot_id and not tickers:
            raise ValueError("universe snapshot has no members")
        raw_memberships = []
        for key in (tickers or sorted(code_by_index)):
            query = {"ts_code": key, "is_new": "Y"} if tickers else {"l3_code": key, "is_new": "Y"}
            try:
                frame = client.index_member_all(**query)
            except Exception as error:
                if "频率超限" not in str(error):
                    raise
                sleep(45)
                frame = client.index_member_all(**query)
            raw_memberships.extend(frame.to_dict("records"))
            sleep(args.request_interval_seconds)
        memberships = membership_rows(raw_memberships, code_by_index)
        artifact_dir = Path(args.artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        raw_path = artifact_dir / f"{version}.raw.json"
        raw_path.write_text(json.dumps({"classifications": classifications, "memberships": raw_memberships,
                                        "universe_snapshot_id": args.universe_snapshot_id}, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
        raw_hash = sha256(raw_path.read_bytes()).hexdigest()
        manifest_path = artifact_dir / f"{version}.manifest.json"
        manifest_hash = write_manifest(manifest_path, {str(raw_path): raw_hash})
        store_taxonomy_version(connection, version, taxonomy, memberships)
    finally:
        connection.close()
    print(json.dumps({"taxonomy_version": version, "industry_count": len(taxonomy), "membership_count": len(memberships), "raw_artifact": str(raw_path), "raw_sha256": raw_hash, "manifest": str(manifest_path), "manifest_sha256": manifest_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
