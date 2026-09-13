from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.margin import OpeningMarginDebt, accrue_financing_interest, create_margin_account, margin_debt_balance
from quant_core.settlement import SettlementService


def test_margin_debt_accrues_simple_daily_interest():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    service.create_account("margin", "两融", Decimal("100"), date(2026, 9, 11))
    create_margin_account(connection, "margin", Decimal("0.365"),
                          (OpeningMarginDebt("600000.SH", Decimal("100"), Decimal("100")),), date(2026, 9, 11))
    assert accrue_financing_interest(connection, "margin", date(2026, 9, 12)) == Decimal("0.1000")
    assert margin_debt_balance(connection, "margin") == Decimal("100.1000")
