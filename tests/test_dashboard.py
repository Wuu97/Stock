from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.dashboard import load_dashboard
from quant_core.models import FeeModel, OrderIntent
from quant_core.settlement import SettlementService
from tests.test_risk import _bar


def test_dashboard_reads_account_and_position_without_mutating_state():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    service.create_account("acct", "测试账户", Decimal("10000"), date(2026, 1, 2))
    order = OrderIntent("buy", "acct", "600000.SH", date(2026, 1, 2), "BUY", 100)
    service.create_intent(order)
    fee = FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    service.settle(order, _bar(date(2026, 1, 2), Decimal("10")), date(2026, 1, 5), fee)
    data = load_dashboard(connection, "acct")
    assert data["account"]["name"] == "测试账户"
    assert data["positions"][0]["ticker"] == "600000.SH"
    assert data["positions"][0]["unrealized_return"] is None
