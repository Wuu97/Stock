"""Archive and synchronize A-share plus ETF ticker-to-name reference data from Tushare."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

from quant_core.database import writer_connection

from quant_core.environment import load_env_file
from quant_core.security_master import SecurityMasterStore, security_name_rows
from quant_core.tushare_source import create_tushare_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--artifact-dir", default="data/security_master")
    parser.add_argument("--skip-etf", action="store_true", help="Synchronize only A-share reference rows.")
    args = parser.parse_args()
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    client = create_tushare_client(token)
    stock_rows = client.stock_basic(exchange="", list_status="L", fields="ts_code,name").to_dict("records")
    etf_rows = [] if args.skip_etf else client.fund_basic(market="E", status="L", fields="ts_code,name").to_dict("records")
    names, etf_names = security_name_rows(stock_rows), security_name_rows(etf_rows)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    raw_path = artifact_dir / f"tushare_stock_basic_{now:%Y%m%d}.raw.json"
    raw_path.write_text(json.dumps({"stock_basic": stock_rows, "fund_basic_etf": etf_rows}, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    raw_hash = sha256(raw_path.read_bytes()).hexdigest()
    connection = writer_connection(args.db, transaction=False)
    try:
        store = SecurityMasterStore(connection)
        stock_count = store.upsert(names, "tushare_stock_basic", str(raw_path), raw_hash, now, now)
        etf_count = store.upsert(etf_names, "tushare_fund_basic", str(raw_path), raw_hash, now, now,
                                 instrument_type="ETF", settlement_cycle="T1", board_lot=100,
                                 price_tick=Decimal("0.001"), price_limit_ratio=Decimal("0.10"),
                                 sell_stamp_duty_rate=Decimal("0"))
    finally:
        connection.close()
    print(json.dumps({"a_share_count": stock_count, "etf_count": etf_count, "raw_artifact": str(raw_path), "raw_sha256": raw_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
