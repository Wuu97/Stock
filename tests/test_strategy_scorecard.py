from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.experiments import ExperimentSpec, ExperimentStore
from quant_core.portfolio import PortfolioPolicy
from quant_core.risk import ExitRule
from quant_core.strategy_research import baseline_strategy_spec
from quant_core.strategy_scorecard import CompetitionProfile, StrategyScorecardStore, competition_coverage, leaderboard


def _fixture():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute("INSERT INTO sim_accounts VALUES ('acc', 'test', 'CNY', 1000, 'rule', 'fifo', 'ACTIVE', ?)", [now])
    connection.execute("INSERT INTO sim_nav_daily VALUES ('acc', '2026-09-01', 1000, 0, 1000, 1, 0), ('acc', '2026-09-02', 1010, 0, 1010, 1.01, 0), ('acc', '2026-09-03', 1020, 0, 1020, 1.02, 0)")
    connection.execute("INSERT INTO sim_order_intents VALUES ('filled', NULL, 'acc', 'AAA', '2026-09-02', 'BUY', 100, 'model', 'FILLED', NULL, ?), ('rejected', NULL, 'acc', 'BBB', '2026-09-02', 'BUY', 100, 'model', 'REJECTED', 'LIMIT_UP_BARRIER', ?)", [now, now])
    connection.execute("INSERT INTO ledger_journal_entries VALUES ('gain', 'j1', 'acc', '2026-09-03', 'sale1', '5001', 'AAA', 0, 10, 'gain', ?), ('cost', 'j1', 'acc', '2026-09-03', 'sale1', '1101', 'AAA', 0, 100, 'cost', ?)", [now, now])
    spec = ExperimentSpec(baseline_strategy_spec(Decimal("1"), 5), PortfolioPolicy.fixed_shares(100), ExitRule(), "cost", date(2026, 9, 1), date(2026, 9, 3), ("snapshot",), "fixture", "BENCH", "fixture_benchmark", Decimal("1000"))
    store = ExperimentStore(connection)
    store.create("experiment", "acc", spec, now)
    store.store_result("experiment", {"nav_observations": 3, "total_return": Decimal(".02"), "annualized_return": Decimal("1"), "excess_return": Decimal(".01"), "max_drawdown": Decimal("0"), "sharpe": Decimal("1"), "average_holding_days": Decimal("2"), "turnover": Decimal(".1")}, now)
    return connection, now


def test_backtest_scorecard_is_immutable_idempotent_and_marks_low_sample():
    connection, now = _fixture()
    store = StrategyScorecardStore(connection)
    profile = CompetitionProfile("large_cap_top5", "v1", {"universe_reference": "fixture", "top_n": 5})
    first = store.store_experiment_scorecard("experiment", profile, "BACKTEST", now)
    second = store.store_experiment_scorecard("experiment", profile, "BACKTEST", now)
    assert first == second
    metrics, status = connection.execute("SELECT metrics_json, sample_status FROM strategy_scorecards").fetchone()
    assert '"fill_rate":"0.5"' in metrics
    assert '"expectancy":"0.1"' in metrics
    assert '"rolling_sharpe_window_days":60' in metrics
    assert status == "LOW_SAMPLE"
    with pytest.raises(ValueError, match="immutable content"):
        store.ensure_profile(CompetitionProfile("large_cap_top5", "v1", {"top_n": 10}), now)


def test_leaderboard_is_profile_stage_scoped_and_hides_low_samples_by_default():
    connection, now = _fixture()
    store = StrategyScorecardStore(connection)
    profile = CompetitionProfile("large_cap_top5", "v1", {"universe_reference": "fixture", "top_n": 5})
    store.store_experiment_scorecard("experiment", profile, "BACKTEST", now)
    assert leaderboard(connection, "large_cap_top5", "v1", "BACKTEST") == []
    rows = leaderboard(connection, "large_cap_top5", "v1", "BACKTEST", include_non_sufficient=True)
    assert rows[0]["strategy_id"] == "historical_momentum_v1"
    assert rows[0]["sample_status"] == "LOW_SAMPLE"


def test_competition_coverage_requires_all_frozen_controls_and_sufficient_samples():
    connection, now = _fixture()
    store = StrategyScorecardStore(connection)
    profile = CompetitionProfile("large_cap_top5", "v1", {"universe_reference": "fixture", "top_n": 5})
    store.store_experiment_scorecard("experiment", profile, "BACKTEST", now)
    coverage = competition_coverage(connection, "large_cap_top5", "v1", "BACKTEST")
    assert "pure_momentum_v1" in coverage["missing_strategies"]
    assert coverage["non_sufficient"][0]["strategy_id"] == "historical_momentum_v1"
    assert not coverage["ready_for_first_comparison"]
