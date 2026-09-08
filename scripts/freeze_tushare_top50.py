"""Freeze a Tushare current-market-cap snapshot for the large-cap universe."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path

import duckdb
import tushare as ts

from quant_core.environment import load_env_file
from quant_core.universe import UniverseService


TUSHARE_MARKET_CAP_UNIT = Decimal("10000")  # daily_basic.total_mv is reported in 10k CNY.


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--min-total-market-cap", default="80000000000", help="CNY")
    args = parser.parse_args()

    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")

    minimum = Decimal(args.min_total_market_cap)
    frame = ts.pro_api(token).daily_basic(fields="ts_code,trade_date,total_mv")
    if frame.empty or not {"ts_code", "trade_date", "total_mv"}.issubset(frame.columns):
        raise RuntimeError("Tushare daily_basic returned no usable market-cap data")
    values = {
        str(row.ts_code): Decimal(str(row.total_mv)) * TUSHARE_MARKET_CAP_UNIT
        for row in frame.itertuples(index=False)
        if Decimal(str(row.total_mv)) * TUSHARE_MARKET_CAP_UNIT >= minimum
    }

    now = datetime.now(timezone.utc)
    connection = duckdb.connect(args.db)
    try:
        UniverseService(connection).store_market_caps(
            args.snapshot_id, now, "tushare_daily_basic", values, now
        )
    finally:
        connection.close()
    print(frame.iloc[0]["trade_date"], len(values), args.snapshot_id)


if __name__ == "__main__":
    main()
