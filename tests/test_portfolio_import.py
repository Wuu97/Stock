from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.portfolio_import import OpeningHolding, import_opening_portfolio


def test_opening_portfolio_import_creates_sellable_lots_and_preserves_cash(tmp_path):
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    source = tmp_path / "portfolio.json"
    source.write_text("{}", encoding="utf-8")
    import_opening_portfolio(connection, "cash", "普通账户", Decimal("10"), date(2026, 9, 11),
                             (OpeningHolding("512400.SH", 100, Decimal("2")),), source)
    assert connection.execute("SELECT cash_balance FROM sim_account_opening_snapshots").fetchone()[0] == Decimal("10.0000")
    assert connection.execute("SELECT available_from_date FROM sim_position_lots").fetchone()[0] == date(2026, 9, 11)
    assert connection.execute("SELECT SUM(debit_amount - credit_amount) FROM ledger_journal_entries WHERE account_code = '1001'").fetchone()[0] == Decimal("10.0000")
