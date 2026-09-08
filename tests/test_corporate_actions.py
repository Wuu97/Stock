from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.corporate_actions import CorporateActionService, PRETAX_VERSION
from quant_core.models import DayBar, FeeModel, OrderIntent
from quant_core.settlement import SettlementService


def _buy(service, account_id):
    order = OrderIntent("buy", account_id, "600000.SH", date(2026, 1, 2), "BUY", 100)
    service.create_intent(order)
    bar = DayBar(date(2026, 1, 2), "600000.SH", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"), 100, Decimal("1000"), Decimal("11"), Decimal("9"))
    service.settle(order, bar, date(2026, 1, 5), FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")))


def test_pretax_dividend_is_entitled_on_record_date_and_paid_later():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    settlement = SettlementService(connection)
    settlement.create_account("acct", "test", Decimal("10000"), date(2026, 1, 1)); _buy(settlement, "acct")
    actions = CorporateActionService(connection)
    action = actions.register("600000.SH", "CASH_DIVIDEND", record_date=date(2026, 1, 5), payment_date=date(2026, 1, 10), cash_per_share=Decimal("1"))
    assert actions.capture_dividend_entitlements(action, "acct") == Decimal("100.0000")
    assert actions.pay_dividends(action, "acct", date(2026, 1, 10)) == Decimal("100.0000")
    assert connection.execute("SELECT SUM(debit_amount-credit_amount) FROM ledger_journal_entries WHERE account_id='acct' AND account_code='1001'").fetchone()[0] == Decimal("9100.0000")
    assert connection.execute("SELECT tax_assumption_version FROM sim_corporate_action_events").fetchone()[0] == PRETAX_VERSION
