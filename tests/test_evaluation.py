from datetime import date, timedelta
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.evaluation import EvaluationService, evaluate_execution, summarize
from quant_core.models import DayBar


def _bar(day, ticker, open_price, close):
    return DayBar(day, ticker, open_price, open_price, open_price, close, 1, Decimal("1"), open_price + 1, open_price - 1)


def test_evaluation_uses_trading_day_horizons_and_benchmark_excess_return():
    execution_day = date(2026, 9, 1)
    stock = [_bar(execution_day + timedelta(days=index), "AAA", Decimal("10"), Decimal("10") + index)
             for index in range(1, 21)]
    benchmark = [_bar(execution_day, "BENCH", Decimal("100"), Decimal("100"))] + [
        _bar(execution_day + timedelta(days=index), "BENCH", Decimal("100"), Decimal("100"))
        for index in range(1, 21)
    ]
    result = evaluate_execution(Decimal("10"), execution_day, stock, benchmark)
    assert result.returns[1] == Decimal("0.1")
    assert result.excess_returns[1] == Decimal("0.1")
    assert result.returns[20] == Decimal("2")
    assert result.max_close_drawdown == Decimal("0")


def test_summary_reports_execution_rate_and_observed_win_rate():
    result = evaluate_execution(Decimal("10"), date(2026, 9, 1), [
        _bar(date(2026, 9, 2), "AAA", Decimal("9"), Decimal("9"))
    ], [_bar(date(2026, 9, 1), "BENCH", Decimal("100"), Decimal("100")),
          _bar(date(2026, 9, 2), "BENCH", Decimal("100"), Decimal("100"))])
    summary = summarize([(True, result), (False, result)])
    assert summary["execution_rate"] == Decimal("0.5")
    assert summary["t1_win_rate"] == Decimal("0")


def test_evaluation_service_persists_executed_and_unexecuted_recommendations():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    con.execute("INSERT INTO sim_accounts VALUES ('acc', 'test', 'CNY', 1000, 'rule', 'fifo', 'ACTIVE', ?)", [now])
    con.execute("INSERT INTO feature_snapshots VALUES ('feature', '2026-09-01', 20, 'input', 'artifact', 'hash', ?, ?)", [now, now])
    con.execute("INSERT INTO recommendation_runs VALUES ('run', '2026-09-02', 'strategy', 'v1', 'cost', 'feature', ?, 'FROZEN', NULL, ?)", [now, now])
    con.execute("INSERT INTO recommendation_items VALUES ('item-a', 'run', 'AAA', 1, 1, 10, '{}', ?), ('item-b', 'run', 'BBB', 2, 0, 10, '{}', ?)", [now, now])
    con.execute("INSERT INTO sim_order_intents VALUES ('intent', 'item-a', 'acc', 'AAA', '2026-09-02', 'BUY', 100, 'NEXT_OPEN_WITH_SLIPPAGE', 'FILLED', NULL, ?)", [now])
    con.execute("INSERT INTO sim_executions VALUES ('exec', 'intent', 'acc', '2026-09-02', 'AAA', 'BUY', 10, 100, 1000, 5, 0, 0, FALSE, 'cost', ?)", [now])
    bars = [_bar(date(2026, 9, 2), "BENCH", Decimal("100"), Decimal("100")),
            _bar(date(2026, 9, 3), "BENCH", Decimal("100"), Decimal("101")),
            _bar(date(2026, 9, 3), "AAA", Decimal("11"), Decimal("11"))]
    summary = EvaluationService(con).evaluate_run("run", bars, "BENCH", "v1")
    assert summary["execution_rate"] == Decimal("0.5")
    rows = con.execute("SELECT is_executed, t1_abs_return FROM performance_evaluations ORDER BY is_executed").fetchall()
    assert rows == [(False, None), (True, Decimal("0.10000000"))]
