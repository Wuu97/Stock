from datetime import date, datetime, timezone
from decimal import Decimal

from quant_core.derived_evaluation import DerivedEvaluationRunner, validate_replay
from quant_core.experiments import BenchmarkClose
from tests.test_strategy_scorecard import _fixture


def _profile():
    return {"profile_id": "derived", "profile_version": "v1", "universe_binding": {},
            "market_data_binding": {}, "replay_assumptions": {"fee": "f"}}


def test_derived_evaluation_resumes_each_owned_stage_without_mutating_experiment():
    connection, now = _fixture()
    profile = _profile()
    connection.execute("INSERT INTO competition_profiles VALUES ('derived','v1',?,?,?)", ['{}', 'profile-hash', now])
    runner = DerivedEvaluationRunner(connection)
    bars = [BenchmarkClose(date(2026, 9, day), Decimal(str(close))) for day, close in ((1, 100), (2, 101), (3, 102))]
    evaluation_id, scorecard_id = runner.run(source_experiment_id='experiment', source_profile=profile,
        evaluation_profile=profile, benchmark_bars=bars, benchmark_dataset_hash='benchmark-hash',
        benchmark_identifier='BENCH', benchmark_version='v1', evaluation_method_version='derived_v1', created_at=now)
    assert connection.execute("SELECT status FROM strategy_evaluations WHERE evaluation_id=?", [evaluation_id]).fetchone()[0] == 'SCORECARD_COMPLETE'
    assert connection.execute("SELECT source_evaluation_id FROM strategy_scorecards WHERE scorecard_id=?", [scorecard_id]).fetchone()[0] == evaluation_id
    assert connection.execute("SELECT count(*) FROM strategy_experiment_results").fetchone()[0] == 1
    assert runner.run(source_experiment_id='experiment', source_profile=profile, evaluation_profile=profile,
        benchmark_bars=bars, benchmark_dataset_hash='benchmark-hash', benchmark_identifier='BENCH',
        benchmark_version='v1', evaluation_method_version='derived_v1', created_at=now) == (evaluation_id, scorecard_id)


def test_replay_validator_rejects_calendar_and_account_lineage():
    connection, _ = _fixture()
    profile = _profile() | {"market_data_binding": {"source": "missing"}}
    try:
        validate_replay(connection, 'acc', '2026-09-01', '2026-09-03', experiment_id='experiment', profile=profile)
    except ValueError as error:
        assert 'calendar coverage' in str(error)
    else:
        raise AssertionError('expected fail-closed calendar validation')
