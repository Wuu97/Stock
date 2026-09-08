from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.daily_risk import create_exit_intents
from quant_core.models import DayBar
from quant_core.risk import ExitRule, evaluate_exit
from quant_core.settlement import SettlementService
from quant_core.models import FeeModel, OrderIntent


def _bar(day, close):
    return DayBar(day, "600000.SH", close, close, close, close, 100, Decimal("1000"), Decimal("20"), Decimal("1"))


def test_stop_loss_uses_close_confirmation():
    signal = evaluate_exit("600000.SH", Decimal("10"), [_bar(date(2026, 1, 2), Decimal("8.9"))], ExitRule())
    assert signal and signal.reason == "STOP_LOSS_CLOSE"


def test_trailing_take_profit_requires_profit_gate_and_drawdown():
    signal = evaluate_exit("600000.SH", Decimal("10"), [
        _bar(date(2026, 1, 2), Decimal("12")), _bar(date(2026, 1, 5), Decimal("11.4")),
    ], ExitRule())
    assert signal and signal.reason == "TRAILING_TAKE_PROFIT_CLOSE"


def test_max_holding_day_is_fallback_after_price_rules():
    signal = evaluate_exit("600000.SH", Decimal("10"), [
        _bar(date(2026, 1, 2), Decimal("10")), _bar(date(2026, 1, 5), Decimal("10")),
    ], ExitRule(max_holding_days=2))
    assert signal and signal.reason == "MAX_HOLDING_DAYS"


def test_exit_signal_creates_one_next_open_sell_intent():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    service.create_account("acct", "test", Decimal("10000"), date(2026, 1, 2))
    buy = OrderIntent("buy", "acct", "600000.SH", date(2026, 1, 2), "BUY", 100)
    service.create_intent(buy)
    fee = FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    service.settle(buy, _bar(date(2026, 1, 2), Decimal("10")), date(2026, 1, 5), fee)
    bars = [_bar(date(2026, 1, 2), Decimal("10")), _bar(date(2026, 1, 5), Decimal("8.9"))]
    first = create_exit_intents(connection, "acct", date(2026, 1, 5), date(2026, 1, 6), bars, ExitRule())
    repeated = create_exit_intents(connection, "acct", date(2026, 1, 5), date(2026, 1, 6), bars, ExitRule())
    assert first == (("600000.SH", "STOP_LOSS_CLOSE"),)
    assert repeated == ()
    assert connection.execute("SELECT direction, target_trade_date FROM sim_order_intents WHERE intent_id != 'buy'").fetchone() == (
        "SELL", date(2026, 1, 6)
    )
