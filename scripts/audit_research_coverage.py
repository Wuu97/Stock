"""Fail-closed coverage audit for the inputs required by historical research."""

import argparse
import csv
from datetime import date
import json
from pathlib import Path

import duckdb


def _dates(values):
    return sorted(values)


def _intervals(days):
    if not days:
        return []
    ranges, start, previous = [], days[0], days[0]
    for current in days[1:]:
        if (current - previous).days > 4:
            ranges.append([str(start), str(previous)])
            start = current
        previous = current
    ranges.append([str(start), str(previous)])
    return ranges


def _adjusted_pairs(path: Path):
    with path.open(encoding="utf-8", newline="") as source:
        rows = csv.DictReader(source)
        required = {"trade_date", "ticker", "adj_close"}
        if not rows.fieldnames or not required.issubset(rows.fieldnames):
            raise ValueError("adjusted CSV must contain trade_date,ticker,adj_close")
        return {(_parse_date(row["trade_date"]), row["ticker"]) for row in rows if row["adj_close"]}


def _parse_date(value: str) -> date:
    text = str(value)
    return date.fromisoformat(text) if "-" in text else date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--adjusted-csv", required=True)
    parser.add_argument("--benchmark-adjusted-csv", required=True)
    parser.add_argument("--benchmark-ticker", default="000300.SH")
    parser.add_argument("--market-source-channel", default="tushare_history_daily")
    parser.add_argument("--trading-status-source-channel", default="tushare_suspend_d")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    adjusted, benchmark = _adjusted_pairs(Path(args.adjusted_csv)), _adjusted_pairs(Path(args.benchmark_adjusted_csv))
    connection = duckdb.connect(args.db, read_only=True)
    try:
        pit_rows = connection.execute(
            "SELECT u.as_of_trade_date, m.ticker FROM universe_snapshots u JOIN universe_members m ON m.universe_snapshot_id = u.universe_snapshot_id "
            "WHERE u.group_name = ? AND u.as_of_trade_date BETWEEN ? AND ? ORDER BY 1, 2", [args.group_name, start, end]
        ).fetchall()
        market_days = {row[0] for row in connection.execute("SELECT trade_date FROM market_data_snapshots WHERE source_channel = ? AND trade_date BETWEEN ? AND ?", [args.market_source_channel, start, end]).fetchall()}
        status_ranges = connection.execute("SELECT start_trade_date, end_trade_date FROM trading_status_snapshots WHERE source_channel = ?", [args.trading_status_source_channel]).fetchall()
        bars = {(day, ticker): ok for day, ticker, ok in connection.execute(
            "SELECT b.trade_date, b.ticker, b.limit_up IS NOT NULL AND b.limit_down IS NOT NULL AND b.open > 0 AND b.high > 0 AND b.low > 0 AND b.close > 0 "
            "FROM daily_bars b JOIN market_data_snapshots s ON s.market_snapshot_id = b.market_snapshot_id "
            "WHERE s.source_channel = ? AND b.trade_date BETWEEN ? AND ?", [args.market_source_channel, start, end]
        ).fetchall()}
    finally:
        connection.close()
    members = {}
    for day, ticker in pit_rows:
        members.setdefault(day, set()).add(ticker)
    rows, common = [], []
    for day in _dates(members):
        tickers = members[day]
        missing_execution = sorted(ticker for ticker in tickers if not bars.get((day, ticker), False))
        missing_adjusted = sorted(ticker for ticker in tickers if (day, ticker) not in adjusted)
        status_covered = any(begin <= day <= finish for begin, finish in status_ranges)
        benchmark_ok = (day, args.benchmark_ticker) in benchmark
        complete = day in market_days and not missing_execution and not missing_adjusted and benchmark_ok and status_covered
        if complete:
            common.append(day)
        rows.append({"trade_date": str(day), "pit_members": len(tickers), "market_snapshot": day in market_days,
                     "execution_missing_count": len(missing_execution), "adjusted_missing_count": len(missing_adjusted),
                     "benchmark_adjusted": benchmark_ok, "trading_status_covered": status_covered, "complete": complete,
                     "execution_missing_sample": missing_execution[:5], "adjusted_missing_sample": missing_adjusted[:5]})
    payload = {"scope": vars(args), "pit_days": len(members), "fully_complete_days": len(common),
               "complete_intervals": _intervals(common), "daily": rows}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "pit_days": len(members), "fully_complete_days": len(common), "complete_intervals": payload["complete_intervals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
