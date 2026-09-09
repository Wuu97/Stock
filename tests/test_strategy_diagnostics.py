from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.experiments import BenchmarkClose
from quant_core.strategy_diagnostics import RegimeConfig, calculate_strategy_diagnostics


def test_strategy_diagnostics_uses_persisted_nav_exits_and_real_benchmark():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    connection.execute("INSERT INTO sim_accounts VALUES ('acc', 'test', 'CNY', 1000, 'rule', 'fifo', 'ACTIVE', now())")
    connection.execute("INSERT INTO sim_nav_daily VALUES "
                       "('acc', '2026-01-02', 1000, 0, 1000, 1, 0), "
                       "('acc', '2026-01-05', 1010, 0, 1010, 1.01, 0), "
                       "('acc', '2026-01-06', 1000, 0, 1000, 1, .01), "
                       "('acc', '2026-01-07', 1020, 0, 1020, 1.02, .01)")
    connection.execute("INSERT INTO sim_order_intents VALUES ('sell', NULL, 'acc', 'AAA', '2026-01-07', 'SELL', 100, 'model', 'FILLED', NULL, now())")
    connection.execute("INSERT INTO sim_exit_signals VALUES ('signal', 'sell', 'acc', 'AAA', '2026-01-06', '2026-01-07', 'STOP_LOSS_CLOSE', 9, 10, now())")
    benchmark = [BenchmarkClose(date(2026, 1, 2), Decimal('100')), BenchmarkClose(date(2026, 1, 5), Decimal('101')),
                 BenchmarkClose(date(2026, 1, 6), Decimal('100')), BenchmarkClose(date(2026, 1, 7), Decimal('102'))]

    report = calculate_strategy_diagnostics(connection, 'acc', benchmark, RegimeConfig(2, Decimal('0.005')))

    assert report['exit_outcomes']['STOP_LOSS_CLOSE']['filled'] == 1
    assert report['regime_observations']['WARMUP'] == 1
    assert report['regime_observations']['UP'] == 1
