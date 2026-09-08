"""Capture a current AKShare total-market-cap snapshot for dynamic monitoring pools."""

import argparse
from datetime import datetime, timezone
from uuid import uuid4

import duckdb

from quant_core.akshare_source import current_total_market_caps
from quant_core.universe import UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--snapshot-id", default=None)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    snapshot_id = args.snapshot_id or str(uuid4())
    connection = duckdb.connect(args.db)
    UniverseService(connection).store_market_caps(snapshot_id, now, "akshare_spot_em", current_total_market_caps(), now)
    connection.close()
    print(snapshot_id)


if __name__ == "__main__":
    main()
