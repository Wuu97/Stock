"""Fail-closed integrity audit for a BaoStock historical ST evidence run."""

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

from quant_core.database import read_connection


def _raw_rows(path: Path, ticker: str):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("ticker") != ticker or not isinstance(payload.get("rows"), list):
        raise ValueError("raw artifact has an invalid BaoStock ST shape")
    rows = []
    for value in payload["rows"]:
        if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], bool):
            raise ValueError("raw artifact has an invalid normalized ST row")
        rows.append((date.fromisoformat(value[0]), value[1]))
    if len({day for day, _ in rows}) != len(rows):
        raise ValueError("raw artifact has duplicate ticker/date ST facts")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    with read_connection(args.db) as connection:
        run = connection.execute("SELECT source_policy_version FROM st_history_backfill_runs WHERE backfill_run_id=?", [args.run_id]).fetchone()
        if not run or run[0] != "baostock_is_st_supplier_fact_v1":
            raise ValueError("run is not a BaoStock ST supplier-fact evidence run")
        states = connection.execute("SELECT ticker,status,row_count,raw_artifact_path,raw_artifact_sha256 FROM st_history_backfill_state WHERE backfill_run_id=? ORDER BY ticker", [args.run_id]).fetchall()
        if not states or any(status != "SUCCEEDED" for _, status, _, _, _ in states):
            raise ValueError("BaoStock ST run has non-succeeded tickers")
        rebuilt_count = 0
        for ticker, _, expected_count, raw_path, expected_hash in states:
            path = Path(raw_path)
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"raw artifact hash mismatch for {ticker}")
            raw = _raw_rows(path, ticker)
            database = connection.execute("SELECT trade_date,is_st FROM st_history_daily WHERE backfill_run_id=? AND ticker=? ORDER BY trade_date", [args.run_id, ticker]).fetchall()
            if raw != database or len(raw) != expected_count:
                raise ValueError(f"normalized ST facts do not match raw artifact for {ticker}")
            rebuilt_count += len(raw)
    print(json.dumps({"run_id": args.run_id, "ticker_artifacts": len(states), "rebuilt_st_facts": rebuilt_count,
                      "artifact_integrity": "PASS", "raw_to_normalized_integrity": "PASS"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
