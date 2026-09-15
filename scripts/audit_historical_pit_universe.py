"""Read-only semantic and coverage audit for an immutable historical PIT universe."""

import argparse
import json
from datetime import date

from quant_core.database import read_connection


def _rows(connection, sql, params):
    return connection.execute(sql, params).fetchall()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--listing-snapshot-id", required=True)
    parser.add_argument("--st-backfill-run-id", required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    with read_connection(args.db) as connection:
        snapshots = _rows(connection, """
            SELECT universe_snapshot_id, as_of_trade_date, rule_version, rule_json, listing_snapshot_id, st_backfill_run_id
            FROM universe_snapshots WHERE group_name=? AND as_of_trade_date BETWEEN ? AND ? ORDER BY as_of_trade_date
        """, [args.group_name, start, end])
        member_counts = _rows(connection, """
            SELECT u.as_of_trade_date, COUNT(m.ticker) FROM universe_snapshots u
            LEFT JOIN universe_members m ON m.universe_snapshot_id=u.universe_snapshot_id
            WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1
        """, [args.group_name, start, end])
        candidate_ct = _rows(connection, """
            SELECT COUNT(*) FROM universe_snapshots u JOIN market_cap_values c
            ON c.market_cap_snapshot_id=u.market_cap_snapshot_id
            WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ? AND c.total_market_cap >= 80000000000
        """, [args.group_name, start, end])[0][0]
        listing_unknown, listing_under_60 = _rows(connection, """
            WITH calendar AS (SELECT DISTINCT trade_date FROM market_data_snapshots WHERE source_channel='tushare_history_daily'),
            candidates AS (
              SELECT u.as_of_trade_date, c.ticker FROM universe_snapshots u JOIN market_cap_values c
              ON c.market_cap_snapshot_id=u.market_cap_snapshot_id
              WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ? AND c.total_market_cap >= 80000000000
            ), age AS (
              SELECT x.as_of_trade_date, x.ticker, l.list_date, COUNT(cal.trade_date) AS age_days
              FROM candidates x LEFT JOIN security_listing_values l ON l.listing_snapshot_id=? AND l.ticker=x.ticker
              LEFT JOIN calendar cal ON cal.trade_date BETWEEN l.list_date AND x.as_of_trade_date
              GROUP BY 1,2,3
            ) SELECT COUNT(*) FILTER (WHERE list_date IS NULL), COUNT(*) FILTER (WHERE list_date IS NOT NULL AND age_days < 60) FROM age
        """, [args.group_name, start, end, args.listing_snapshot_id])[0]
        st_true, st_unknown = _rows(connection, """
            WITH candidates AS (
              SELECT u.as_of_trade_date, c.ticker FROM universe_snapshots u JOIN market_cap_values c
              ON c.market_cap_snapshot_id=u.market_cap_snapshot_id
              WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ? AND c.total_market_cap >= 80000000000
            ) SELECT COUNT(*) FILTER (WHERE s.is_st), COUNT(*) FILTER (WHERE s.ticker IS NULL)
              FROM candidates c LEFT JOIN st_history_daily s ON s.backfill_run_id=? AND s.trade_date=c.as_of_trade_date AND s.ticker=c.ticker
        """, [args.group_name, start, end, args.st_backfill_run_id])[0]
        short_history = _rows(connection, """
            WITH candidates AS (
              SELECT u.as_of_trade_date, c.ticker FROM universe_snapshots u JOIN market_cap_values c
              ON c.market_cap_snapshot_id=u.market_cap_snapshot_id
              WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ? AND c.total_market_cap >= 80000000000
            ), bars AS (
              SELECT c.as_of_trade_date, c.ticker, COUNT(b.trade_date) AS n FROM candidates c
              LEFT JOIN (daily_bars b JOIN market_data_snapshots s
                ON s.market_snapshot_id=b.market_snapshot_id AND s.source_channel='tushare_history_daily')
              ON b.ticker=c.ticker AND b.trade_date<=c.as_of_trade_date AND b.status='TRADING' GROUP BY 1,2
            ) SELECT COUNT(*) FROM bars WHERE n < 31
        """, [args.group_name, start, end])[0][0]
    if not snapshots:
        raise ValueError("no snapshots found for requested group/date range")
    expected_rule = {"minimum_total_market_cap": "80000000000", "momentum_window_days": 30,
                     "ranking_metric": "close_return", "top_n": 50, "min_listing_trading_days": 60,
                     "require_non_st": True}
    semantic_ok = all(row[2] == "current_cap_momentum_v1" and json.loads(row[3]) == expected_rule
                      and row[4] == args.listing_snapshot_id and row[5] == args.st_backfill_run_id for row in snapshots)
    counts = [row[1] for row in member_counts]
    print(json.dumps({
        "group_name": args.group_name, "date_range": [args.start_date, args.end_date],
        "snapshot_trading_days": len(snapshots), "member_count": {"min": min(counts), "max": max(counts),
        "average": sum(counts) / len(counts), "distribution": {str(n): counts.count(n) for n in sorted(set(counts))}},
        "candidate_day_count": candidate_ct,
        "listing_evidence": {"unknown": listing_unknown, "known": candidate_ct - listing_unknown, "under_60_trading_days": listing_under_60},
        "st_evidence": {"unknown": st_unknown, "known": candidate_ct - st_unknown, "st_true_excluded": st_true},
        "trading_bar_evidence": {"under_31_trading_bars": short_history},
        "rule_version": snapshots[0][2], "rule_json": json.loads(snapshots[0][3]),
        "listing_snapshot_id": args.listing_snapshot_id, "st_backfill_run_id": args.st_backfill_run_id,
        "semantic_audit_passed": semantic_ok,
    }, ensure_ascii=False, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
