from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.backtest import BacktestConfig, replay_daily_strategy
from quant_core.models import DayBar, FeeModel
from quant_core.risk import ExitRule
from quant_core.settlement import SettlementService


def test_backtest_replays_next_open_orders_without_future_bars():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    start = date(2026, 1, 2)
    days = [start + timedelta(days=index) for index in range(25)]
    bars = [DayBar(day, "600000.SH", Decimal("10"), Decimal("10.2"), Decimal("9.8"),
                   Decimal("10") + Decimal(index) / Decimal("100"), 1000, Decimal("10000"),
                   Decimal("20"), Decimal("1")) for index, day in enumerate(days)]
    SettlementService(connection).create_account("backtest", "test", Decimal("100000"), start)
    result = replay_daily_strategy(
        connection, bars, days,
        BacktestConfig("backtest", days[20], days[-1], top_n=1, volume_multiple=Decimal("0.5")),
        FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        ExitRule(), {"600000.SH"},
    )
    assert result["buy_orders_submitted"] == 1
    assert result["orders"]["BUY_FILLED"] == 1
