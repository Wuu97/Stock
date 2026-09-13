from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.models import DayBar, FeeModel, OrderIntent
from quant_core.security_master import SecurityMasterStore
from quant_core.security_rules import execution_rules
from quant_core.settlement import SettlementService


FEE = FeeModel("fixture", Decimal("0"), Decimal("0"), Decimal("0.0005"), Decimal("0"), Decimal("0"))


def _bar(day, ticker):
    return DayBar(day, ticker, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1000, Decimal("1000"), Decimal("1.1"), Decimal("0.9"))


def test_etf_rules_remove_sell_stamp_duty_and_can_be_explicitly_t0():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    SecurityMasterStore(connection).upsert((("159740.SZ", "恒生科技ETF"),), "fixture", "fixture.json", "a" * 64, now, now,
                                           instrument_type="ETF", settlement_cycle="T0", price_tick=Decimal("0.001"),
                                           price_limit_ratio=Decimal("0.10"), sell_stamp_duty_rate=Decimal("0"))
    rules = execution_rules(connection, "159740.SZ")
    assert rules.settlement_cycle == "T0"
    assert rules.fee(FEE).stamp_duty_rate == Decimal("0")


def test_t0_etf_lot_is_sellable_on_the_buy_trade_date():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    SecurityMasterStore(connection).upsert((("159740.SZ", "恒生科技ETF"),), "fixture", "fixture.json", "a" * 64, now, now,
                                           instrument_type="ETF", settlement_cycle="T0", price_tick=Decimal("0.001"),
                                           price_limit_ratio=Decimal("0.10"), sell_stamp_duty_rate=Decimal("0"))
    service, day = SettlementService(connection), date(2026, 9, 11)
    service.create_account("account", "ETF", Decimal("1000"), day)
    buy = OrderIntent("buy", "account", "159740.SZ", day, "BUY", 100)
    service.create_intent(buy)
    assert service.settle(buy, _bar(day, "159740.SZ"), date(2026, 9, 12), FEE) == "FILLED"
    sell = OrderIntent("sell", "account", "159740.SZ", day, "SELL", 100)
    service.create_intent(sell)
    assert service.settle(sell, _bar(day, "159740.SZ"), date(2026, 9, 12), FEE) == "FILLED"
    assert connection.execute("SELECT stamp_duty FROM sim_executions WHERE intent_id = 'sell'").fetchone()[0] == Decimal("0.0000")
