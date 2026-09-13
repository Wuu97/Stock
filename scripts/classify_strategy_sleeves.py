"""Freeze a horizon-sleeve classification for a configured monitoring group."""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.strategy_sleeves import classify_tickers, load_sleeve_config, store_sleeve_snapshot
from quant_core.universe import FixedUniverseRule, UniverseService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--group-name", default="personal_holdings")
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--config", default="config/strategy_sleeves.json")
    parser.add_argument("--create-fixed-snapshot", action="store_true",
                        help="Create the configured fixed monitoring group if it has not yet been frozen.")
    parser.add_argument("--monitoring-groups-config", default="config/monitoring_groups.json")
    parser.add_argument("--market-cap-snapshot-id")
    args = parser.parse_args()
    config_path = Path(args.config)
    config, config_sha256 = load_sleeve_config(config_path)
    with writer_connection(args.db) as connection:
        row = connection.execute(
            "SELECT universe_snapshot_id FROM universe_snapshots WHERE group_name = ? AND as_of_trade_date = ? "
            "ORDER BY created_at DESC LIMIT 1", [args.group_name, date.fromisoformat(args.as_of_date)]
        ).fetchone()
        if row is None:
            if not args.create_fixed_snapshot or not args.market_cap_snapshot_id:
                raise ValueError("no frozen monitoring-group snapshot exists for this date")
            groups = json.loads(Path(args.monitoring_groups_config).read_text(encoding="utf-8")).get("groups", [])
            group = next((item for item in groups if item.get("name") == args.group_name), None)
            if not group or group.get("type") != "fixed":
                raise ValueError("only a configured fixed group can be created here")
            universe_id = UniverseService(connection).create_fixed_snapshot(
                args.market_cap_snapshot_id, date.fromisoformat(args.as_of_date),
                FixedUniverseRule(args.group_name, group["tickers"]), datetime.now(timezone.utc)
            )
            row = (universe_id,)
        tickers = [item[0] for item in connection.execute(
            "SELECT ticker FROM universe_members WHERE universe_snapshot_id = ?", [row[0]]
        ).fetchall()]
        assignments = classify_tickers(connection, tickers, config)
        snapshot_id = store_sleeve_snapshot(connection, args.group_name, date.fromisoformat(args.as_of_date),
                                             config_path, config_sha256, assignments, datetime.now(timezone.utc))
    print({"sleeve_snapshot_id": snapshot_id, "assignments": [item.__dict__ for item in assignments]})


if __name__ == "__main__":
    main()
