from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.order_lifecycle import ExecutionOrder, OrderLifecycleService
from quant_core.settlement import SettlementService


UTC = timezone.utc


def _connection():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    SettlementService(connection).create_account("acct", "Test", Decimal("100000"), date(2026, 9, 14))
    return connection


def _order(order_id="order-1"):
    return ExecutionOrder(order_id, "acct", "600000.SH", "BUY", "LIMIT", "AUCTION_ONLY", 300,
                          datetime(2026, 9, 14, 9, 24, tzinfo=UTC), "auction_execution_v1",
                          limit_price=Decimal("10.00"), strategy_id="test")


def test_order_lifecycle_records_partial_fill_cancel_and_immutable_audit_events():
    connection = _connection()
    service = OrderLifecycleService(connection)
    order = _order()
    service.submit(order, details={"source": "test"})
    service.activate(order.order_id, datetime(2026, 9, 14, 9, 24, 30, tzinfo=UTC), observed_data_id="auction-a")
    assert service.record_fill(order.order_id, datetime(2026, 9, 14, 9, 25, tzinfo=UTC), Decimal("9.98"), 100,
                               observed_data_id="auction-a") == "PARTIALLY_FILLED"
    service.cancel(order.order_id, datetime(2026, 9, 14, 9, 25, 1, tzinfo=UTC), "AUCTION_WINDOW_EXPIRED")
    assert connection.execute("SELECT order_status, filled_shares, terminal_reason_code FROM execution_orders").fetchone() == (
        "CANCELLED", 100, "AUCTION_WINDOW_EXPIRED"
    )
    assert [row[0] for row in connection.execute("SELECT event_type FROM execution_order_events ORDER BY event_at").fetchall()] == [
        "SUBMITTED", "ACTIVATED", "PARTIAL_FILL", "CANCELLED"
    ]
    assert connection.execute("SELECT deal_shares, deal_price_unadj FROM execution_order_fills").fetchone() == (100, Decimal("9.9800"))


def test_order_lifecycle_rejects_overfill_and_terminal_transition():
    connection = _connection()
    service = OrderLifecycleService(connection)
    order = _order()
    service.submit(order)
    service.activate(order.order_id, datetime(2026, 9, 14, 9, 24, tzinfo=UTC))
    with pytest.raises(ValueError, match="exceeds"):
        service.record_fill(order.order_id, datetime(2026, 9, 14, 9, 25, tzinfo=UTC), Decimal("10"), 400)
    service.record_fill(order.order_id, datetime(2026, 9, 14, 9, 25, tzinfo=UTC), Decimal("10"), 300)
    with pytest.raises(ValueError, match="cannot transition"):
        service.cancel(order.order_id, datetime(2026, 9, 14, 9, 25, 1, tzinfo=UTC), "TOO_LATE")
