"""Fetch a bounded, resumable batch of BaoStock historical ST flags."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import sleep
from uuid import uuid5, NAMESPACE_URL

from quant_core.baostock_st_source import fetch_is_st, session
from quant_core.database import read_connection, writer_connection


SOURCE_CHANNEL = "baostock_is_st_history_v1"
POLICY_VERSION = "baostock_is_st_supplier_fact_v1"


def _run_id(start_date: date, end_date: date) -> str:
    return "bst_" + uuid5(NAMESPACE_URL, f"{SOURCE_CHANNEL}:{POLICY_VERSION}:{start_date}:{end_date}").hex


def _candidate_tickers(db_path: str, run_id: str, start_date: date, end_date: date, limit: int) -> list[str]:
    with read_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT DISTINCT ticker FROM daily_bars WHERE trade_date BETWEEN ? AND ? "
            "AND (ticker LIKE '%.SH' OR ticker LIKE '%.SZ') EXCEPT "
            "SELECT ticker FROM st_history_backfill_state WHERE backfill_run_id = ? AND status = 'SUCCEEDED' "
            "ORDER BY ticker LIMIT ?",
            [start_date, end_date, run_id, limit],
        ).fetchall()
    return [row[0] for row in rows if not row[0].startswith(("200", "900"))]


def _start_attempt(db_path: str, run_id: str, ticker: str) -> None:
    now = datetime.now(timezone.utc)
    with writer_connection(db_path) as connection:
        row = connection.execute(
            "SELECT attempt_count FROM st_history_backfill_state WHERE backfill_run_id = ? AND ticker = ?", [run_id, ticker]
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO st_history_backfill_state VALUES (?, ?, 'RUNNING', 1, NULL, NULL, NULL, NULL, ?)",
                [run_id, ticker, now],
            )
        else:
            connection.execute(
                "UPDATE st_history_backfill_state SET status = 'RUNNING', attempt_count = ?, row_count = NULL, "
                "raw_artifact_path = NULL, raw_artifact_sha256 = NULL, error_text = NULL, updated_at = ? "
                "WHERE backfill_run_id = ? AND ticker = ?",
                [row[0] + 1, now, run_id, ticker],
            )


def _finish_attempt(db_path: str, run_id: str, ticker: str, rows, artifact_path: Path, artifact_hash: str) -> None:
    now = datetime.now(timezone.utc)
    with writer_connection(db_path) as connection:
        connection.execute("DELETE FROM st_history_daily WHERE backfill_run_id = ? AND ticker = ?", [run_id, ticker])
        if rows:
            connection.executemany(
                "INSERT INTO st_history_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(run_id, ticker, trade_date, is_st, str(artifact_path), artifact_hash, now) for trade_date, is_st in rows],
            )
        connection.execute(
            "UPDATE st_history_backfill_state SET status = 'SUCCEEDED', row_count = ?, raw_artifact_path = ?, "
            "raw_artifact_sha256 = ?, error_text = NULL, updated_at = ? WHERE backfill_run_id = ? AND ticker = ?",
            [len(rows), str(artifact_path), artifact_hash, now, run_id, ticker],
        )


def _fail_attempt(db_path: str, run_id: str, ticker: str, error: Exception) -> None:
    with writer_connection(db_path) as connection:
        connection.execute(
            "UPDATE st_history_backfill_state SET status = 'FAILED', error_text = ?, updated_at = ? "
            "WHERE backfill_run_id = ? AND ticker = ?",
            [str(error)[:2000], datetime.now(timezone.utc), run_id, ticker],
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--max-tickers", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--artifact-dir", default="data/st_history/baostock")
    args = parser.parse_args()
    start_date, end_date = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start_date > end_date or args.max_tickers < 1 or args.sleep_seconds < 0:
        raise ValueError("invalid date range or batch configuration")
    run_id = _run_id(start_date, end_date)
    with writer_connection(args.db) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO st_history_backfill_runs VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, SOURCE_CHANNEL, start_date, end_date, POLICY_VERSION, datetime.now(timezone.utc)],
        )
    tickers = _candidate_tickers(args.db, run_id, start_date, end_date, args.max_tickers)
    artifact_dir = Path(args.artifact_dir) / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    completed, failed = [], []
    with session() as client:
        for ticker in tickers:
            _start_attempt(args.db, run_id, ticker)
            try:
                rows = fetch_is_st(client, ticker, start_date, end_date)
                artifact_path = artifact_dir / f"{ticker.replace('.', '_')}.raw.json"
                artifact_path.write_text(json.dumps({"ticker": ticker, "start_date": start_date.isoformat(),
                                                      "end_date": end_date.isoformat(), "rows": rows},
                                                     ensure_ascii=False, default=str, sort_keys=True), encoding="utf-8")
                artifact_hash = sha256(artifact_path.read_bytes()).hexdigest()
                _finish_attempt(args.db, run_id, ticker, rows, artifact_path, artifact_hash)
                completed.append(ticker)
            except Exception as error:
                _fail_attempt(args.db, run_id, ticker, error)
                failed.append({"ticker": ticker, "error": str(error)})
            if args.sleep_seconds:
                sleep(args.sleep_seconds)
    with read_connection(args.db) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT ticker FROM daily_bars WHERE trade_date BETWEEN ? AND ? "
            "AND (ticker LIKE '%.SH' OR ticker LIKE '%.SZ') EXCEPT "
            "SELECT ticker FROM st_history_backfill_state WHERE backfill_run_id = ? AND status = 'SUCCEEDED')",
            [start_date, end_date, run_id],
        ).fetchone()[0]
    print(json.dumps({"backfill_run_id": run_id, "completed_tickers": len(completed), "failed_tickers": failed,
                      "remaining_tickers": remaining, "artifact_dir": str(artifact_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
