"""Run the local-only daily data refresh and record a visible status in DuckDB."""

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from quant_core.database import read_connection, writer_connection
from quant_core.environment import load_env_file
from quant_core.market_data import MarketDataStore
from quant_core.settlement import SettlementService
from quant_core.tushare_source import create_tushare_client


ROOT = Path(__file__).resolve().parents[1]


def _is_open_trade_day(token, trade_date):
    frame = create_tushare_client(token).trade_cal(exchange="SSE", start_date=trade_date.strftime("%Y%m%d"),
                                                    end_date=trade_date.strftime("%Y%m%d"), is_open="1")
    return not frame.empty


def _run(script, arguments):
    completed = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT,
                               text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"{script}: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout.strip()


def _snapshot_exists(db_path, snapshot_id):
    with read_connection(db_path) as connection:
        return connection.execute("SELECT 1 FROM market_data_snapshots WHERE market_snapshot_id = ?", [snapshot_id]).fetchone() is not None


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/top50/quant.duckdb")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    args = parser.parse_args()
    trade_date = date.fromisoformat(args.trade_date)
    load_env_file(ROOT / ".env")
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    run_id, started = str(uuid4()), datetime.now(timezone.utc)
    with writer_connection(args.db) as connection:
        connection.execute("INSERT INTO local_refresh_runs VALUES (?, ?, ?, NULL, 'RUNNING', NULL, NULL, ?)",
                           [run_id, trade_date, started, "{}"])
    try:
        if not _is_open_trade_day(token, trade_date):
            detail = {"reason": "SSE_NON_TRADING_DAY"}
            status, portfolio, top50 = "SKIPPED", None, None
        else:
            portfolio = f"portfolio_market_{trade_date:%Y%m%d}"
            top50 = f"tushare_pool_{trade_date:%Y%m%d}"
            detail = {}
            if _snapshot_exists(args.db, portfolio):
                detail["portfolio"] = "already_exists"
            else:
                detail["portfolio"] = _run("snapshot_portfolio_market_data.py", ["--db", args.db, "--trade-date", str(trade_date)])
            detail["valued_accounts"] = _value_active_accounts(args.db, portfolio, trade_date)
            if _snapshot_exists(args.db, top50):
                detail["top50"] = "already_exists"
            else:
                detail["top50"] = _run("refresh_daily_dynamic_universe.py", ["--db", args.db, "--trade-date", str(trade_date), "--group-name", "large_cap_momentum"])
            status = "SUCCEEDED"
        _finish_run(args.db, run_id, status, portfolio, top50, detail)
        print(json.dumps({"run_id": run_id, "status": status, "portfolio_snapshot_id": portfolio, "top50_snapshot_id": top50}, ensure_ascii=False))
    except Exception as error:
        _finish_run(args.db, run_id, "FAILED", None, None, {"error": str(error)})
        raise


if __name__ == "__main__":
    main()
