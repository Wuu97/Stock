"""Sample-check archived BaoStock ST flags against Tushare name-change history.

This creates an immutable JSON report only.  It never changes the ST history used
by universe gates because a name history is a consistency check, not a replacement
for the daily supplier flag.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from quant_core.database import read_connection
from quant_core.environment import load_env_file
from quant_core.st_history_audit import namechange_st_at
from quant_core.tushare_source import create_tushare_client


def _sample_rows(db_path: str, run_id: str, per_flag: int):
    """Pick a stable, ticker-diverse sample from each BaoStock flag value."""
    with read_connection(db_path) as connection:
        rows = connection.execute(
            "WITH ranked AS ("
            "SELECT ticker, trade_date, is_st, ROW_NUMBER() OVER (PARTITION BY is_st, ticker ORDER BY trade_date DESC) AS n "
            "FROM st_history_daily WHERE backfill_run_id = ?"
            "), representatives AS ("
            "SELECT ticker, trade_date, is_st FROM ranked WHERE n = 1"
            "), sampled AS ("
            "SELECT ticker, trade_date, is_st, ROW_NUMBER() OVER (PARTITION BY is_st ORDER BY ticker) AS sample_n "
            "FROM representatives"
            ") SELECT ticker, trade_date, is_st FROM sampled WHERE sample_n <= ? ORDER BY is_st DESC, ticker",
            [run_id, per_flag],
        ).fetchall()
    # The query is deterministic, but it may contain an uneven split.  Enforce it
    # after reading so a quiet ST period does not get silently reported as sampled.
    samples = {True: [], False: []}
    for ticker, trade_date, is_st in rows:
        flag = bool(is_st)
        if len(samples[flag]) < per_flag:
            samples[flag].append((ticker, trade_date, flag))
    return samples[True] + samples[False]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--backfill-run-id", required=True)
    parser.add_argument("--per-flag", type=int, default=10)
    parser.add_argument("--artifact-dir", default="data/st_history/namechange_audit")
    args = parser.parse_args()
    if args.per_flag < 1:
        raise ValueError("--per-flag must be positive")

    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    samples = _sample_rows(args.db, args.backfill_run_id, args.per_flag)
    if not samples:
        raise ValueError("BaoStock ST history run is missing or contains no rows")

    client = create_tushare_client(token)
    checked_at = datetime.now(timezone.utc)
    results = []
    for ticker, trade_date, baostock_is_st in samples:
        try:
            records = client.namechange(ts_code=ticker, fields="ts_code,name,start_date,end_date").to_dict("records")
            tushare_is_st = namechange_st_at(records, trade_date)
            result = "INCONCLUSIVE" if tushare_is_st is None else (
                "MATCH" if tushare_is_st == baostock_is_st else "MISMATCH"
            )
            results.append({"ticker": ticker, "trade_date": trade_date.isoformat(),
                            "baostock_is_st": baostock_is_st, "tushare_namechange_is_st": tushare_is_st,
                            "result": result, "namechange_records": records})
        except Exception as error:
            results.append({"ticker": ticker, "trade_date": trade_date.isoformat(),
                            "baostock_is_st": baostock_is_st, "result": "QUERY_FAILED", "error": str(error)})

    counts = {name: sum(row["result"] == name for row in results)
              for name in ("MATCH", "MISMATCH", "INCONCLUSIVE", "QUERY_FAILED")}
    report = {"report_type": "baostock_st_vs_tushare_namechange_sample_v1",
              "backfill_run_id": args.backfill_run_id, "checked_at": checked_at.isoformat(),
              "sample_policy": {"per_flag": args.per_flag, "selection": "latest_row_per_ticker_then_ticker_order"},
              "counts": counts, "results": results}
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{args.backfill_run_id}_{checked_at:%Y%m%dT%H%M%SZ}.json"
    artifact_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"report_path": str(artifact_path), "report_sha256": sha256(artifact_path.read_bytes()).hexdigest(),
                      "sampled_rows": len(results), "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
