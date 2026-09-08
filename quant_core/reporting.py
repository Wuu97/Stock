"""Small, deterministic JSON audit reports for frozen recommendation runs."""

from datetime import datetime
import json
from pathlib import Path
from typing import Mapping


def write_report(path: Path, run_id: str, summary: Mapping, created_at: datetime) -> None:
    payload = {"run_id": run_id, "created_at": created_at.isoformat(), "summary": summary}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def daily_monitoring_summary(connection, target_trade_date, account_id=None) -> dict:
    """Summarize frozen recommendations, execution outcomes and explicit failure states."""
    rows = connection.execute(
        "SELECT r.run_id, r.strategy_id, r.run_status, r.invalid_reason_code, COUNT(i.item_id), "
        "COALESCE(SUM(o.order_status = 'FILLED'), 0), COALESCE(SUM(o.order_status = 'REJECTED'), 0), "
        "COALESCE(SUM(o.order_status = 'PENDING'), 0) "
        "FROM recommendation_runs r LEFT JOIN recommendation_items i ON i.run_id = r.run_id "
        "LEFT JOIN sim_order_intents o ON o.recommendation_item_id = i.item_id "
        "WHERE r.target_trade_date = ? GROUP BY 1, 2, 3, 4 ORDER BY r.strategy_id", [target_trade_date]
    ).fetchall()
    rejects = connection.execute(
        "SELECT o.reject_reason_code, COUNT(*) FROM sim_order_intents o "
        "JOIN recommendation_items i ON i.item_id = o.recommendation_item_id "
        "JOIN recommendation_runs r ON r.run_id = i.run_id "
        "WHERE r.target_trade_date = ? AND o.order_status = 'REJECTED' GROUP BY 1 ORDER BY 1", [target_trade_date]
    ).fetchall()
    summary = {
        "target_trade_date": str(target_trade_date),
        "runs": [
            {"run_id": run_id, "strategy_id": strategy_id, "status": status, "invalid_reason": reason,
             "recommendations": count, "filled": filled, "rejected": rejected, "pending": pending}
            for run_id, strategy_id, status, reason, count, filled, rejected, pending in rows
        ],
        "reject_reasons": {reason: count for reason, count in rejects},
    }
    if account_id:
        nav = connection.execute(
            "SELECT cash_balance, securities_value, total_equity, unit_nav, max_drawdown FROM sim_nav_daily "
            "WHERE account_id = ? AND trade_date = ?", [account_id, target_trade_date]
        ).fetchone()
        summary["account_id"] = account_id
        summary["nav"] = None if nav is None else dict(zip(
            ("cash_balance", "securities_value", "total_equity", "unit_nav", "max_drawdown"), nav
        ))
    return summary


def write_daily_report(path: Path, summary: Mapping, created_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"created_at": created_at.isoformat(), "summary": summary}, default=str,
                               ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
