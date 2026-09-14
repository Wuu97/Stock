"""Publish paper-account NAV from the day's immutable Tushare pipeline snapshot."""

import argparse
from datetime import date, datetime, timezone
import json
from uuid import uuid4

from quant_core.database import read_connection, writer_connection
from quant_core.market_data import MarketDataStore
from quant_core.settlement import SettlementService


def _finish_run(db_path, run_id, status, portfolio, top50, detail):
    with writer_connection(db_path) as connection:
        connection.execute("UPDATE local_refresh_runs SET completed_at=?, status=?, portfolio_snapshot_id=?, top50_snapshot_id=?, detail_json=? WHERE run_id=?",
                           [datetime.now(timezone.utc), status, portfolio, top50, json.dumps(detail, ensure_ascii=False), run_id])


def _value_active_accounts(db_path, snapshot_id, trade_date):
    """Create one NAV mark for every active simulated account from one shared snapshot."""
    with writer_connection(db_path, transaction=False) as connection:
        bars = MarketDataStore(connection).load_bars(snapshot_id)
        prices = {bar.ticker: bar.close for bar in bars}
        accounts = connection.execute(
            "SELECT account_id FROM sim_accounts WHERE account_status = 'ACTIVE' ORDER BY account_id"
        ).fetchall()
        service = SettlementService(connection)
        for account_id, in accounts:
            service.value_day(account_id, trade_date, prices)
        return [account_id for account_id, in accounts]


def _completed_pipeline_snapshot(db_path, trade_date):
    snapshot_id = f"tushare_pool_{trade_date:%Y%m%d}_limits"
    with read_connection(db_path) as connection:
        snapshot = connection.execute(
            "SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]
        ).fetchone()
        if snapshot is None:
            raise ValueError("completed daily pipeline limit-enriched snapshot is missing")
        completed = connection.execute(
            "SELECT 1 FROM pipeline_runs WHERE trade_date = ? AND run_status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS')",
            [trade_date],
        ).fetchone()
        if completed is None:
            raise ValueError("daily pipeline is not completed; refusing to publish account NAV")
    return snapshot_id


def _already_published(db_path, trade_date, snapshot_id):
    with read_connection(db_path) as connection:
        return connection.execute(
            "SELECT run_id FROM local_refresh_runs WHERE trade_date = ? AND status = 'SUCCEEDED' "
            "AND portfolio_snapshot_id = ? ORDER BY completed_at DESC LIMIT 1",
            [trade_date, snapshot_id],
        ).fetchone()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--market-snapshot-id", help="Only an immutable limit-enriched Tushare snapshot is accepted.")
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    pipeline_snapshot = _completed_pipeline_snapshot(args.db, trade_date)
    if args.market_snapshot_id and args.market_snapshot_id != pipeline_snapshot:
        raise ValueError("local refresh must use the completed pipeline's limit-enriched snapshot")
    existing = _already_published(args.db, trade_date, pipeline_snapshot)
    if existing:
        print(json.dumps({"run_id": existing[0], "status": "ALREADY_PUBLISHED",
                          "portfolio_snapshot_id": pipeline_snapshot}, ensure_ascii=False))
        return
    run_id, started = str(uuid4()), datetime.now(timezone.utc)
    with writer_connection(args.db) as connection:
        connection.execute("INSERT INTO local_refresh_runs VALUES (?, ?, ?, NULL, 'RUNNING', NULL, NULL, ?)",
                           [run_id, trade_date, started, "{}"])
    try:
        detail = {"valued_accounts": _value_active_accounts(args.db, pipeline_snapshot, trade_date)}
        _finish_run(args.db, run_id, "SUCCEEDED", pipeline_snapshot, pipeline_snapshot, detail)
        print(json.dumps({"run_id": run_id, "status": "SUCCEEDED", "portfolio_snapshot_id": pipeline_snapshot,
                          "top50_snapshot_id": pipeline_snapshot}, ensure_ascii=False))
    except Exception as error:
        _finish_run(args.db, run_id, "FAILED", None, None, {"error": str(error)})
        raise


if __name__ == "__main__":
    main()
