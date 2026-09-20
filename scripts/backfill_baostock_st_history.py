"""Fetch a bounded, resumable batch of BaoStock historical ST flags."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import sleep
from typing import Optional
from uuid import uuid5, NAMESPACE_URL

from quant_core.baostock_st_source import fetch_is_st, session
from quant_core.database import read_connection, writer_connection


SOURCE_CHANNEL = "baostock_is_st_history_v1"
POLICY_VERSION = "baostock_is_st_supplier_fact_v1"


def _run_id(start_date: date, end_date: date, candidate_market_source: str, minimum_market_cap: str) -> str:
    return "bst_" + uuid5(NAMESPACE_URL,
                            f"{SOURCE_CHANNEL}:{POLICY_VERSION}:{start_date}:{end_date}:{candidate_market_source}:{minimum_market_cap}").hex


def _candidate_tickers(db_path: str, run_id: str, candidate_market_source: str,
                       minimum_market_cap: str, limit: int,
                       candidate_start: Optional[date] = None, candidate_end: Optional[date] = None) -> list[str]:
    """Only fetch ticker histories that entered the frozen PIT cap candidate domain.

    This intentionally avoids claiming ST completeness for all A shares.  Later
    pre-Universe coverage validation remains responsible for proving explicit
    TRUE/FALSE facts for every actual candidate ticker/date pair.
    """
    with read_connection(db_path) as connection:
        date_clause = ""
        params = [candidate_market_source, minimum_market_cap]
        if candidate_start is not None and candidate_end is not None:
            date_clause = " AND s.market_cap_snapshot_id IN (SELECT 'oos2020_tushare_cap_' || replace(CAST(trade_date AS VARCHAR), '-', '') FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ?)"
            params.extend([candidate_market_source, candidate_start, candidate_end])
        rows = connection.execute(
            "SELECT DISTINCT v.ticker FROM market_cap_values v JOIN market_cap_snapshots s "
            "ON s.market_cap_snapshot_id=v.market_cap_snapshot_id "
            "WHERE s.source_channel=? AND v.total_market_cap>=? AND (v.ticker LIKE '%.SH' OR v.ticker LIKE '%.SZ')" + date_clause + " EXCEPT "
            "SELECT ticker FROM st_history_backfill_state WHERE backfill_run_id = ? AND status = 'SUCCEEDED' "
            "ORDER BY ticker LIMIT ?",
            params + [run_id, limit],
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


def _finish_attempt(db_path: str, run_id: str, ticker: str, rows, artifact_path: Path, artifact_hash: str) -> bool:
    """Append first-seen supplier facts only; never replace a completed fact set."""
    now = datetime.now(timezone.utc)
    with writer_connection(db_path) as connection:
        state = connection.execute(
            "SELECT status, raw_artifact_sha256 FROM st_history_backfill_state WHERE backfill_run_id=? AND ticker=?",
            [run_id, ticker],
        ).fetchone()
        facts = connection.execute(
            "SELECT DISTINCT raw_artifact_sha256 FROM st_history_daily WHERE backfill_run_id=? AND ticker=?",
            [run_id, ticker],
        ).fetchall()
        known_hashes = {value[0] for value in facts}
        if state and state[0] == "SUCCEEDED" and state[1] == artifact_hash and known_hashes in (set(), {artifact_hash}):
            return False
        if (state and state[1] and state[1] != artifact_hash) or known_hashes - {artifact_hash}:
            raise RuntimeError(f"EVIDENCE_CONFLICT: existing BaoStock evidence differs for {run_id} {ticker}")
        if facts:
            # A prior interrupted completion already wrote facts.  Its hash must
            # match; only restore state metadata, never rewrite those facts.
            connection.execute(
                "UPDATE st_history_backfill_state SET status='SUCCEEDED', row_count=?, raw_artifact_path=?, "
                "raw_artifact_sha256=?, error_text=NULL, updated_at=? WHERE backfill_run_id=? AND ticker=?",
                [len(rows), str(artifact_path), artifact_hash, now, run_id, ticker],
            )
            return False
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
    return True


def _store_raw_artifact(artifact_path: Path, raw_bytes: bytes, artifact_hash: str) -> None:
    """Write-once raw evidence.  A different response is retained as conflict evidence."""
    if artifact_path.exists():
        existing = sha256(artifact_path.read_bytes()).hexdigest()
        if existing == artifact_hash:
            return
        conflict = artifact_path.parent / "conflicts" / f"{artifact_path.stem}_{artifact_hash[:20]}{artifact_path.suffix}"
        conflict.parent.mkdir(parents=True, exist_ok=True)
        if not conflict.exists():
            conflict.write_bytes(raw_bytes)
        raise RuntimeError(f"EVIDENCE_CONFLICT: raw artifact already differs; candidate preserved at {conflict}")
    artifact_path.write_bytes(raw_bytes)


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
    parser.add_argument("--candidate-market-source", required=True,
                        help="Frozen market source whose PIT market caps define the ST collection ticker scope.")
    parser.add_argument("--minimum-candidate-market-cap", default="80000000000")
    parser.add_argument("--candidate-start-date")
    parser.add_argument("--candidate-end-date")
    args = parser.parse_args()
    start_date, end_date = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    candidate_start = date.fromisoformat(args.candidate_start_date) if args.candidate_start_date else None
    candidate_end = date.fromisoformat(args.candidate_end_date) if args.candidate_end_date else None
    if (candidate_start is None) != (candidate_end is None) or candidate_start and candidate_start > candidate_end:
        raise ValueError("candidate date bounds must be supplied together and ordered")
    if start_date > end_date or args.max_tickers < 1 or args.sleep_seconds < 0:
        raise ValueError("invalid date range or batch configuration")
    run_id = _run_id(start_date, end_date, args.candidate_market_source, args.minimum_candidate_market_cap)
    with writer_connection(args.db) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO st_history_backfill_runs VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, SOURCE_CHANNEL, start_date, end_date, POLICY_VERSION, datetime.now(timezone.utc)],
        )
    tickers = _candidate_tickers(args.db, run_id, args.candidate_market_source,
                                 args.minimum_candidate_market_cap, args.max_tickers,
                                 candidate_start, candidate_end)
    artifact_dir = Path(args.artifact_dir) / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    completed, failed = [], []
    with session() as client:
        for ticker in tickers:
            _start_attempt(args.db, run_id, ticker)
            try:
                rows = fetch_is_st(client, ticker, start_date, end_date)
                artifact_path = artifact_dir / f"{ticker.replace('.', '_')}.raw.json"
                raw_bytes = json.dumps({"ticker": ticker, "start_date": start_date.isoformat(),
                                        "end_date": end_date.isoformat(), "rows": rows}, ensure_ascii=False,
                                       default=str, sort_keys=True).encode("utf-8")
                artifact_hash = sha256(raw_bytes).hexdigest()
                _store_raw_artifact(artifact_path, raw_bytes, artifact_hash)
                if _finish_attempt(args.db, run_id, ticker, rows, artifact_path, artifact_hash):
                    completed.append(ticker)
            except Exception as error:
                _fail_attempt(args.db, run_id, ticker, error)
                failed.append({"ticker": ticker, "error": str(error)})
            if args.sleep_seconds:
                sleep(args.sleep_seconds)
    with read_connection(args.db) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT v.ticker FROM market_cap_values v JOIN market_cap_snapshots s "
            "ON s.market_cap_snapshot_id=v.market_cap_snapshot_id "
            "WHERE s.source_channel=? AND v.total_market_cap>=? AND (v.ticker LIKE '%.SH' OR v.ticker LIKE '%.SZ') EXCEPT "
            "SELECT ticker FROM st_history_backfill_state WHERE backfill_run_id = ? AND status = 'SUCCEEDED')",
            [args.candidate_market_source, args.minimum_candidate_market_cap, run_id],
        ).fetchone()[0]
    print(json.dumps({"backfill_run_id": run_id, "completed_tickers": len(completed), "failed_tickers": failed,
                      "remaining_tickers": remaining, "artifact_dir": str(artifact_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
