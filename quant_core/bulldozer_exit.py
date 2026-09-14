"""Auditable mandatory-next-trading-day exits for the daily bulldozer proxy."""

from datetime import date, datetime, timezone
from typing import Optional
from uuid import uuid4

from .models import OrderIntent
from .settlement import SettlementService


MANDATORY_EXIT_CODE = "T_PLUS_1_MANDATORY_CLOSE"


def ensure_bulldozer_exit_schema(connection) -> None:
    """Apply the small, idempotent strategy-owned schema migration for old research DBs."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS bulldozer_exit_plans ("
        "plan_id VARCHAR PRIMARY KEY, lot_id VARCHAR NOT NULL UNIQUE REFERENCES sim_position_lots(lot_id), "
        "account_id VARCHAR NOT NULL REFERENCES sim_accounts(account_id), entry_trade_date DATE NOT NULL, "
        "required_exit_trade_date DATE NOT NULL, current_target_trade_date DATE NOT NULL, "
        "active_intent_id VARCHAR NOT NULL REFERENCES sim_order_intents(intent_id), "
        "defer_count INTEGER NOT NULL CHECK (defer_count >= 0), "
        "plan_status VARCHAR NOT NULL CHECK (plan_status IN ('PENDING', 'FILLED', 'BLOCKED')), "
        "exit_reason_code VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL)"
    )


def schedule_mandatory_exits_for_new_lots(connection, account_id: str, entry_trade_date: date,
                                          exit_trade_date: date) -> int:
    """Schedule one next-day sell per newly filled lot, before it becomes sellable.

    The plan is separate from the immutable accounting lot so the generic inventory
    model does not acquire strategy-specific lifecycle fields.
    """
    if exit_trade_date <= entry_trade_date:
        raise ValueError("mandatory exit must be after entry date")
    rows = connection.execute(
        "SELECT lot_id, ticker, orig_shares FROM sim_position_lots WHERE account_id = ? AND buy_trade_date = ? "
        "AND lot_id NOT IN (SELECT lot_id FROM bulldozer_exit_plans)", [account_id, entry_trade_date]
    ).fetchall()
    service = SettlementService(connection)
    now = datetime.now(timezone.utc)
    for lot_id, ticker, shares in rows:
        intent_id = str(uuid4())
        service.create_intent(OrderIntent(intent_id, account_id, ticker, exit_trade_date, "SELL", shares))
        connection.execute(
            "INSERT INTO bulldozer_exit_plans VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'PENDING', ?, ?)",
            [str(uuid4()), lot_id, account_id, entry_trade_date, exit_trade_date, exit_trade_date, intent_id,
             MANDATORY_EXIT_CODE, now],
        )
    return len(rows)


def reconcile_mandatory_exit_plans(connection, account_id: str, as_of_trade_date: date,
                                   next_trade_date: Optional[date]) -> int:
    """Close filled plans or resubmit only objectively blocked mandatory exits."""
    rows = connection.execute(
        "SELECT p.plan_id, p.lot_id, p.defer_count, o.order_status, o.reject_reason_code "
        "FROM bulldozer_exit_plans p JOIN sim_order_intents o ON o.intent_id = p.active_intent_id "
        "WHERE p.account_id = ? AND p.current_target_trade_date = ? AND p.plan_status = 'PENDING'",
        [account_id, as_of_trade_date],
    ).fetchall()
    service = SettlementService(connection)
    deferred = 0
    for plan_id, lot_id, defer_count, order_status, reason in rows:
        if order_status == "FILLED":
            connection.execute("UPDATE bulldozer_exit_plans SET plan_status = 'FILLED' WHERE plan_id = ?", [plan_id])
            continue
        if order_status != "REJECTED":
            continue
        if reason not in {"LIMIT_DOWN_BARRIER", "SUSPENDED"} or next_trade_date is None:
            connection.execute("UPDATE bulldozer_exit_plans SET plan_status = 'BLOCKED' WHERE plan_id = ?", [plan_id])
            continue
        ticker, shares = connection.execute(
            "SELECT ticker, orig_shares FROM sim_position_lots WHERE lot_id = ?", [lot_id]
        ).fetchone()
        intent_id = str(uuid4())
        service.create_intent(OrderIntent(intent_id, account_id, ticker, next_trade_date, "SELL", shares))
        connection.execute(
            "UPDATE bulldozer_exit_plans SET current_target_trade_date = ?, active_intent_id = ?, "
            "defer_count = ?, plan_status = 'PENDING' WHERE plan_id = ?",
            [next_trade_date, intent_id, defer_count + 1, plan_id],
        )
        deferred += 1
    return deferred
