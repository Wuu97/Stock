"""Auditable state machine for auction and intraday simulated orders."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4


TERMINAL_STATUSES = {"FILLED", "CANCELLED", "REJECTED", "EXPIRED"}


@dataclass(frozen=True)
class ExecutionOrder:
    order_id: str
    account_id: str
    ticker: str
    direction: str
    order_type: str
    time_in_force: str
    target_shares: int
    submitted_at: datetime
    execution_rule_version: str
    limit_price: Optional[Decimal] = None
    strategy_id: Optional[str] = None
    parent_order_id: Optional[str] = None


class OrderLifecycleService:
    """Transitions orders one way and appends an immutable event for each change."""

    def __init__(self, connection):
        self.connection = connection

    def submit(self, order: ExecutionOrder, *, details: Optional[dict] = None) -> None:
        if order.direction not in {"BUY", "SELL"} or order.target_shares <= 0:
            raise ValueError("invalid order direction or size")
        if order.order_type not in {"LIMIT", "MARKET"} or order.time_in_force not in {"AUCTION_ONLY", "DAY"}:
            raise ValueError("invalid order type or time in force")
        if (order.order_type == "LIMIT") != (order.limit_price is not None):
            raise ValueError("limit orders require a limit price and market orders do not")
        if order.limit_price is not None and order.limit_price <= 0:
            raise ValueError("limit price must be positive")
        now = self._now()
        self.connection.execute(
            "INSERT INTO execution_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SUBMITTED', 0, NULL, ?, ?, ?)",
            [order.order_id, order.account_id, order.strategy_id, order.parent_order_id, order.ticker,
             order.direction, order.order_type, order.time_in_force, order.target_shares, order.limit_price,
             order.submitted_at, order.execution_rule_version, now, now],
        )
        self._event(order.order_id, "SUBMITTED", order.submitted_at, details=details)

    def activate(self, order_id: str, event_at: datetime, *, observed_data_id: Optional[str] = None,
                 details: Optional[dict] = None) -> None:
        self._transition(order_id, {"SUBMITTED"}, "ACTIVE", "ACTIVATED", event_at,
                         observed_data_id=observed_data_id, details=details)

    def record_fill(self, order_id: str, fill_at: datetime, price: Decimal, shares: int,
                    *, observed_data_id: Optional[str] = None, details: Optional[dict] = None) -> str:
        if shares <= 0 or price <= 0:
            raise ValueError("fill price and shares must be positive")
        row = self._row(order_id)
        if row[0] not in {"ACTIVE", "PARTIALLY_FILLED"}:
            raise ValueError("only active orders can be filled")
        status, target, filled = row[0], int(row[1]), int(row[2])
        if filled + shares > target:
            raise ValueError("fill exceeds remaining order size")
        next_filled = filled + shares
        next_status = "FILLED" if next_filled == target else "PARTIALLY_FILLED"
        event_type = "FILLED" if next_status == "FILLED" else "PARTIAL_FILL"
        now = self._now()
        self.connection.execute(
            "INSERT INTO execution_order_fills VALUES (?, ?, ?, ?, ?, ?, ?)",
            [str(uuid4()), order_id, fill_at, price, shares, observed_data_id, now],
        )
        self.connection.execute(
            "UPDATE execution_orders SET order_status = ?, filled_shares = ?, updated_at = ? WHERE order_id = ?",
            [next_status, next_filled, now, order_id],
        )
        self._event(order_id, event_type, fill_at, observed_data_id=observed_data_id, details=details)
        return next_status

    def cancel(self, order_id: str, event_at: datetime, reason_code: str,
               *, observed_data_id: Optional[str] = None, details: Optional[dict] = None) -> None:
        self._transition(order_id, {"SUBMITTED", "ACTIVE", "PARTIALLY_FILLED"}, "CANCELLED", "CANCELLED", event_at,
                         reason_code=reason_code, observed_data_id=observed_data_id, details=details)

    def reject(self, order_id: str, event_at: datetime, reason_code: str,
               *, observed_data_id: Optional[str] = None, details: Optional[dict] = None) -> None:
        self._transition(order_id, {"SUBMITTED", "ACTIVE"}, "REJECTED", "REJECTED", event_at,
                         reason_code=reason_code, observed_data_id=observed_data_id, details=details)

    def expire(self, order_id: str, event_at: datetime, reason_code: str = "TIME_IN_FORCE_EXPIRED") -> None:
        self._transition(order_id, {"SUBMITTED", "ACTIVE", "PARTIALLY_FILLED"}, "EXPIRED", "EXPIRED", event_at,
                         reason_code=reason_code)

    def _transition(self, order_id, allowed, status, event_type, event_at, *, reason_code=None,
                    observed_data_id=None, details=None) -> None:
        current = self._row(order_id)[0]
        if current not in allowed:
            raise ValueError(f"cannot transition {current} order to {status}")
        now = self._now()
        self.connection.execute(
            "UPDATE execution_orders SET order_status = ?, terminal_reason_code = ?, updated_at = ? WHERE order_id = ?",
            [status, reason_code, now, order_id],
        )
        self._event(order_id, event_type, event_at, reason_code=reason_code,
                    observed_data_id=observed_data_id, details=details)

    def _row(self, order_id):
        row = self.connection.execute(
            "SELECT order_status, target_shares, filled_shares FROM execution_orders WHERE order_id = ?", [order_id]
        ).fetchone()
        if row is None:
            raise ValueError("unknown execution order")
        return row

    def _event(self, order_id, event_type, event_at, *, reason_code=None, observed_data_id=None, details=None) -> None:
        now = self._now()
        self.connection.execute(
            "INSERT INTO execution_order_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [str(uuid4()), order_id, event_type, event_at, observed_data_id, reason_code,
             json.dumps(details or {}, sort_keys=True, ensure_ascii=False), now],
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
