from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.models import DayBar, FeeModel, OrderIntent
from quant_core.settlement import SettlementService


FEE = FeeModel("cost_v1", Decimal("0.00025"), Decimal("5"), Decimal("0.0005"), Decimal("0.00001"), Decimal("0"))


def _bar(day, ticker, open_price, close):
    return DayBar(day, ticker, open_price, open_price, open_price, close, 100, Decimal("100000"), open_price + 1, open_price - 1)


def test_settlement_writes_events_and_daily_valuation():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(con)
    account = "acct"
    service.create_account(account, "test", Decimal("100000"), date(2026, 9, 1))
    buy = OrderIntent("buy", account, "600000.SH", date(2026, 9, 2), "BUY", 100)
    service.create_intent(buy)
    assert service.settle(buy, _bar(date(2026, 9, 2), buy.ticker, Decimal("10"), Decimal("10.5")), date(2026, 9, 3), FEE) == "FILLED"
    sell = OrderIntent("sell", account, "600000.SH", date(2026, 9, 3), "SELL", 100)
    service.create_intent(sell)
    assert service.settle(sell, _bar(date(2026, 9, 3), sell.ticker, Decimal("11"), Decimal("11")), date(2026, 9, 4), FEE) == "FILLED"
    service.value_day(account, date(2026, 9, 3), {})
    nav = con.execute("SELECT cash_balance, securities_value, total_equity FROM sim_nav_daily").fetchone()
    assert tuple(map(Decimal, map(str, nav))) == (Decimal("100089.4290"), Decimal("0.0000"), Decimal("100089.4290"))


def test_settlement_rejects_same_day_sale_without_partial_writes():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(con)
    service.create_account("acct", "test", Decimal("100000"), date(2026, 9, 1))
    buy = OrderIntent("buy", "acct", "600000.SH", date(2026, 9, 2), "BUY", 100)
    service.create_intent(buy)
    service.settle(buy, _bar(date(2026, 9, 2), buy.ticker, Decimal("10"), Decimal("10")), date(2026, 9, 3), FEE)
    sell = OrderIntent("sell", "acct", "600000.SH", date(2026, 9, 2), "SELL", 100)
    service.create_intent(sell)
    assert service.settle(sell, _bar(date(2026, 9, 2), sell.ticker, Decimal("10"), Decimal("10")), date(2026, 9, 3), FEE) == "REJECTED"
    assert con.execute("SELECT COUNT(*) FROM sim_executions WHERE direction = 'SELL'").fetchone()[0] == 0


def test_settlement_carries_only_an_official_suspension_last_close():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(con)
    service.create_account("acct", "test", Decimal("100000"), date(2026, 9, 1))
    buy = OrderIntent("buy", "acct", "600000.SH", date(2026, 9, 2), "BUY", 100)
    service.create_intent(buy)
    service.settle(buy, _bar(date(2026, 9, 2), buy.ticker, Decimal("10"), Decimal("10")), date(2026, 9, 3), FEE)

    service.value_day("acct", date(2026, 9, 3), {}, {"600000.SH"},
                      {"600000.SH": (date(2026, 9, 2), Decimal("10"))})

    mark = con.execute("SELECT mark_price, source_trade_date, mark_basis FROM sim_suspension_valuation_events").fetchone()
    assert (Decimal(str(mark[0])), mark[1], mark[2]) == (Decimal("10.0000"), date(2026, 9, 2), "OFFICIAL_SUSPENSION_LAST_CLOSE")
    with pytest.raises(ValueError, match="missing official close"):
        service.value_day("acct", date(2026, 9, 4), {})
