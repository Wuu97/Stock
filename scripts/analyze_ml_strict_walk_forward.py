"""Audit strict ML walk-forward windows using immutable simulation-ledger data."""

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import duckdb


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--report", required=True, action="append")
    parser.add_argument("--benchmark-csv", default="data/ml_history/000300_SH_adjusted_closes.csv")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    benchmark = _read_benchmark(Path(args.benchmark_csv))
    periods = _read_periods(args.report)
    connection = duckdb.connect(args.db, read_only=True)
    try:
        result = []
        for period in periods:
            if period.get("status") != "COMPLETED":
                continue
            start, end = (date.fromisoformat(value) for value in period["test_dates"])
            benchmark_return = _benchmark_return(benchmark, start, end)
            item = {
                "test_dates": period["test_dates"],
                "market_regime": _market_regime(benchmark_return),
                "benchmark_return": benchmark_return,
                "cash": {"final_equity": "1000000", "max_drawdown": "0", "execution_count": 0,
                         "gross_turnover": "0", "total_cost": "0", "weighted_closed_holding_days": 0,
                         "closed_lot_count": 0},
            }
            for label in ("ml", "ml_guard", "baseline"):
                if label in period:
                    item[label] = _account_metrics(connection, period[label])
            result.append(item)
    finally:
        connection.close()
    payload = {
        "schema_version": "ml_strict_walk_forward_audit_v1",
        "periods": result,
        "note": "Returns and drawdowns come from strict replay reports; costs and holding periods come from immutable execution and lot ledgers.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"periods": len(result), "output": str(output)}, ensure_ascii=False))


def _read_periods(paths):
    periods = []
    for raw_path in paths:
        periods.extend(json.loads(Path(raw_path).read_text(encoding="utf-8")).get("periods", []))
    return periods


def _read_benchmark(path: Path):
    rows = {}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            rows[date.fromisoformat(row["trade_date"])] = float(row["adj_close"])
    return rows


def _benchmark_return(prices, start: date, end: date):
    days = sorted(day for day in prices if start <= day <= end)
    if len(days) < 2:
        return None
    return (prices[days[-1]] / prices[days[0]]) - 1


def _market_regime(value):
    if value is None:
        return "UNKNOWN"
    if value >= 0.03:
        return "RISING"
    if value <= -0.03:
        return "FALLING"
    return "RANGE"


def _account_metrics(connection, report_side):
    account_id = report_side["account_id"]
    executions = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(gross_amount), 0), COALESCE(SUM(commission + stamp_duty + transfer_fee), 0) "
        "FROM sim_executions WHERE account_id = ?", [account_id]
    ).fetchone()
    holding = connection.execute(
        "SELECT COALESCE(SUM(date_diff('day', l.buy_trade_date, d.trade_date) * d.shares_deducted) / NULLIF(SUM(d.shares_deducted), 0), 0), "
        "COUNT(DISTINCT d.lot_id) FROM sim_lot_disposal_events d JOIN sim_position_lots l ON l.lot_id = d.lot_id "
        "WHERE l.account_id = ?", [account_id]
    ).fetchone()
    return {
        "account_id": account_id,
        "final_equity": report_side["final_equity"],
        "max_drawdown": report_side["max_drawdown"],
        "execution_count": executions[0],
        "gross_turnover": str(executions[1]),
        "total_cost": str(executions[2]),
        "weighted_closed_holding_days": float(holding[0]),
        "closed_lot_count": holding[1],
    }


if __name__ == "__main__":
    main()
