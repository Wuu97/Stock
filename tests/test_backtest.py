from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

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


def test_backtest_fails_closed_when_point_in_time_universe_is_missing():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    start = date(2026, 1, 2)
    days = [start + timedelta(days=index) for index in range(23)]
    bars = [DayBar(day, "600000.SH", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"),
                   100, Decimal("1000"), Decimal("11"), Decimal("9")) for day in days]
    SettlementService(connection).create_account("backtest", "test", Decimal("10000"), start)
    with pytest.raises(ValueError, match="point-in-time universe snapshot is missing"):
        replay_daily_strategy(connection, bars, days, BacktestConfig("backtest", days[20], days[-1]),
                              FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
                              ExitRule(), universe_by_date={days[20]: {"600000.SH"}})


def test_backtest_allows_missing_universe_for_final_valuation_day():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    start = date(2026, 1, 2)
    days = [start + timedelta(days=index) for index in range(23)]
    bars = [DayBar(day, "600000.SH", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"),
                   100, Decimal("1000"), Decimal("11"), Decimal("9")) for day in days]
    SettlementService(connection).create_account("backtest", "test", Decimal("10000"), start)
    decision_days = days[20:-1]
    result = replay_daily_strategy(
        connection, bars, days, BacktestConfig("backtest", days[20], days[-1]),
        FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        ExitRule(), universe_by_date={day: {"600000.SH"} for day in decision_days},
    )
    assert result["buy_orders_submitted"] >= 0
