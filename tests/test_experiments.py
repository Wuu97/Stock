from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.experiments import ExperimentSpec, ExperimentStore, calculate_metrics
from quant_core.models import DayBar
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule
from quant_core.strategy_research import baseline_strategy_spec


def _bar(day, close):
    return DayBar(day, "BENCH", close, close, close, close, 1, Decimal("1"), close + 1, close - 1)


def test_experiment_spec_is_immutable_and_metrics_use_persisted_nav_and_orders():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    connection.execute("INSERT INTO sim_accounts VALUES ('acc', 'test', 'CNY', 1000, 'rule', 'fifo', 'ACTIVE', ?)", [now])
    connection.execute("INSERT INTO sim_nav_daily VALUES ('acc', '2026-09-01', 1000, 0, 1000, 1, 0), ('acc', '2026-09-02', 1100, 0, 1100, 1.1, 0), ('acc', '2026-09-03', 990, 0, 990, .99, .1)")
    connection.execute("INSERT INTO sim_order_intents VALUES ('filled', NULL, 'acc', 'AAA', '2026-09-02', 'BUY', 100, 'model', 'FILLED', NULL, ?), ('rejected', NULL, 'acc', 'BBB', '2026-09-02', 'BUY', 100, 'model', 'REJECTED', 'LIMIT_UP_BARRIER', ?)", [now, now])
    connection.execute("INSERT INTO sim_executions VALUES ('exec', 'filled', 'acc', '2026-09-02', 'AAA', 'BUY', 10, 100, 1000, 0, 0, 0, FALSE, 'cost', ?)", [now])
    spec = ExperimentSpec(baseline_strategy_spec(Decimal("1"), 5), PortfolioPolicy.fixed_shares(100), ExitRule(), "cost", date(2026, 9, 1), date(2026, 9, 3), ("snapshot",), "fixture", "BENCH", Decimal("1000"))
    store = ExperimentStore(connection)
    store.create("experiment", "acc", spec, now)
    metrics = calculate_metrics(connection, "acc", [_bar(date(2026, 9, 1), Decimal("100")), _bar(date(2026, 9, 2), Decimal("105")), _bar(date(2026, 9, 3), Decimal("99"))])
    store.store_result("experiment", metrics, now)
    assert metrics["total_return"] == Decimal("-0.01")
    assert metrics["benchmark_return"] == Decimal("-0.01")
    assert metrics["execution_rate"] == Decimal("0.5")
    assert connection.execute("SELECT spec_sha256 FROM strategy_experiments").fetchone()[0]
    assert connection.execute("SELECT metrics_sha256 FROM strategy_experiment_results").fetchone()[0]
