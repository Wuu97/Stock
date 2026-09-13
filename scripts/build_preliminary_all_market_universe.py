"""Freeze a resumable all-A preliminary eligibility universe from PIT daily data.

It deliberately does *not* claim historical ST exclusion: only Beijing-board,
official-current-day suspension, missing/non-trading bars, and liquidity are applied.
"""

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json

from quant_core.database import writer_connection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--min-average-amount", default="50000000")
    parser.add_argument("--max-days", type=int, help="Build at most this many missing trading dates; safe to resume.")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    minimum = Decimal(args.min_average_amount)
    if start > end or args.lookback_days < 2 or minimum <= 0 or args.max_days is not None and args.max_days < 1:
        raise ValueError("invalid preliminary-universe arguments")
    exclusions = json.dumps({
        "beijing_board": True, "official_current_day_suspension": True,
        "non_trading_or_missing_current_bar": True, "min_average_amount": str(minimum),
        "historical_st_excluded": False,
    }, sort_keys=True)
    with writer_connection(args.db) as connection:
        target_dates = [row[0] for row in connection.execute(
            "SELECT DISTINCT trade_date FROM market_data_snapshots WHERE source_channel = 'tushare_history_daily' "
            "AND trade_date BETWEEN ? AND ? AND trade_date NOT IN "
            "(SELECT as_of_trade_date FROM preliminary_universe_snapshots) ORDER BY trade_date",
            [start, end],
        ).fetchall()]
        if args.max_days is not None:
            target_dates = target_dates[:args.max_days]
        if not target_dates:
            print(json.dumps({"built": 0, "status": "NO_MISSING_DATES"}, ensure_ascii=False))
            return
        placeholders = ",".join("?" for _ in target_dates)
        query = """
        WITH bars AS (
            SELECT s.trade_date, b.ticker, b.amount, b.status,
                   AVG(b.amount) OVER (PARTITION BY b.ticker ORDER BY s.trade_date
                     ROWS BETWEEN ? PRECEDING AND CURRENT ROW) AS average_amount,
                   COUNT(*) OVER (PARTITION BY b.ticker ORDER BY s.trade_date
                     ROWS BETWEEN ? PRECEDING AND CURRENT ROW) AS observed_days
            FROM daily_bars b JOIN market_data_snapshots s ON s.market_snapshot_id = b.market_snapshot_id
            WHERE s.source_channel = 'tushare_history_daily' AND s.trade_date <= ?
              AND (b.ticker LIKE '%.SH' OR b.ticker LIKE '%.SZ') AND b.ticker NOT LIKE '%.BJ'
        ), eligible AS (
            SELECT b.trade_date, b.ticker, b.average_amount, b.observed_days
            FROM bars b
            LEFT JOIN trading_status_events e ON e.ticker = b.ticker AND e.trade_date = b.trade_date
            LEFT JOIN trading_status_snapshots ts ON ts.trading_status_snapshot_id = e.trading_status_snapshot_id
              AND ts.source_channel = 'tushare_suspend_d' AND e.status_code = 'SUSPENDED'
            WHERE b.trade_date IN (""" + placeholders + """) AND b.status = 'TRADING'
              AND ts.trading_status_snapshot_id IS NULL AND b.observed_days = ? AND b.average_amount >= ?
        )
        SELECT trade_date, ticker, average_amount, observed_days FROM eligible ORDER BY trade_date, ticker
        """
        rows = connection.execute(query, [args.lookback_days - 1, args.lookback_days - 1, max(target_dates),
                                           *target_dates, args.lookback_days, minimum]).fetchall()
        now = datetime.now(timezone.utc)
        connection.executemany("INSERT INTO preliminary_universe_snapshots VALUES (?, ?, ?, ?, ?, 'PRELIMINARY', ?, ?)", [
            (f"preliminary_all_a_{day:%Y%m%d}", day, "tushare_history_daily", args.lookback_days,
             minimum, exclusions, now) for day in target_dates
        ])
        connection.executemany("INSERT INTO preliminary_universe_members VALUES (?, ?, ?, ?)", [
            (f"preliminary_all_a_{day:%Y%m%d}", ticker, average_amount, observed_days)
            for day, ticker, average_amount, observed_days in rows
        ])
    counts = {day.isoformat(): 0 for day in target_dates}
    for day, *_ in rows:
        counts[day.isoformat()] += 1
    print(json.dumps({"built": len(target_dates), "eligible_counts": counts,
                      "classification_status": "PRELIMINARY"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
