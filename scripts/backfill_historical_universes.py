"""Create missing PIT Top50 universe snapshots from already archived daily inputs."""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal

from quant_core.database import writer_connection

from quant_core.market_data import MarketDataStore
from quant_core.universe import DynamicUniverseRule, UniverseService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--group-name", default="historical_large_cap_momentum")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    rule = DynamicUniverseRule(args.group_name, Decimal("80000000000"), 30, 50, "historical_pit_cap_momentum_v1")
    connection = writer_connection(args.db, transaction=False)
    try:
        days = [row[0] for row in connection.execute(
            "SELECT DISTINCT trade_date FROM daily_bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date", [start, end]
        ).fetchall()]
        service, store, created = UniverseService(connection), MarketDataStore(connection), []
        for day in days:
            exists = connection.execute("SELECT 1 FROM universe_snapshots WHERE group_name = ? AND as_of_trade_date = ?", [args.group_name, day]).fetchone()
            if exists:
                continue
            cap_id = f"tushare_history_cap_{day:%Y%m%d}"
            if connection.execute("SELECT 1 FROM market_cap_snapshots WHERE market_cap_snapshot_id = ?", [cap_id]).fetchone() is None:
                raise RuntimeError(f"market cap snapshot is missing for {day}")
            source_ids = [row[0] for row in connection.execute(
                "SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel = 'tushare_history_daily' "
                "AND trade_date <= ? ORDER BY trade_date DESC LIMIT 31", [day]
            ).fetchall()]
            if len(source_ids) < 31:
                raise RuntimeError(f"insufficient trailing history for {day}")
            service.create_snapshot(cap_id, day, rule, store.load_bars_many(reversed(source_ids)), datetime.now(timezone.utc))
            created.append(day.isoformat())
    finally:
        connection.close()
    print({"created": created, "count": len(created)})


if __name__ == "__main__":
    main()
