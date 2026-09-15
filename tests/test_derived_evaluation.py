from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from quant_core.derived_evaluation import (DerivedEvaluationRunner, execution_fingerprint,
                                           validate_replay)
from quant_core.experiments import BenchmarkClose
from tests.test_strategy_scorecard import _fixture


def _payload(benchmark_hash='benchmark-hash', replay='f', market=None):
    return {"universe_binding": {}, "market_data_binding": market or {}, "replay_assumptions": {"fee": replay},
            "benchmark_binding": {"identifier": "BENCH", "version": "v1", "dataset_hash": benchmark_hash}}


def _put_profile(connection, profile_id, version, payload, now, digest=None):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    connection.execute("INSERT INTO competition_profiles VALUES (?,?,?,?,?)", [profile_id, version, text, digest or sha256(text.encode()).hexdigest(), now])


def _bars(values=((1, 100), (2, 101), (3, 102))):
    return [BenchmarkClose(date(2026, 9, day), Decimal(str(close))) for day, close in values]


def _runner_fixture():
    connection, now = _fixture(); payload = _payload(market={"source": "fixture"})
    for day in (1, 2, 3):
        connection.execute("INSERT INTO market_data_snapshots VALUES (?,?,?,?,?,?,?,?)", [f'm{day}', date(2026, 9, day), 'fixture', now, now, 'fixture', f'h{day}', now])
    _put_profile(connection, 'source', 'v1', payload, now); _put_profile(connection, 'evaluation', 'v1', payload, now)
    return connection, now, DerivedEvaluationRunner(connection)


def _run(runner, now, bars=None, **overrides):
    args = dict(source_experiment_id='experiment', source_profile_id='source', source_profile_version='v1',
                evaluation_profile_id='evaluation', evaluation_profile_version='v1', benchmark_bars=bars or _bars(),
                benchmark_dataset_hash='benchmark-hash', benchmark_identifier='BENCH', benchmark_version='v1',
                evaluation_method_version='derived_v1', created_at=now)
    args.update(overrides); return runner.run(**args)


def test_frozen_profiles_drive_evaluation_and_completed_evaluation_is_idempotent(monkeypatch):
    connection, now, runner = _runner_fixture()
    before = connection.execute("SELECT count(*),sum(total_equity) FROM sim_nav_daily WHERE account_id='acc'").fetchone()
    experiment_before = connection.execute("SELECT spec_json FROM strategy_experiments WHERE experiment_id='experiment'").fetchone()
    result_before = connection.execute("SELECT metrics_json FROM strategy_experiment_results WHERE experiment_id='experiment'").fetchone()
    import quant_core.backtest
    monkeypatch.setattr(quant_core.backtest, 'replay_daily_strategy', lambda *a, **k: pytest.fail('derived evaluation must never replay'))
    evaluation_id, scorecard_id = _run(runner, now)
    assert connection.execute("SELECT status FROM strategy_evaluations WHERE evaluation_id=?", [evaluation_id]).fetchone()[0] == 'SCORECARD_COMPLETE'
    assert connection.execute("SELECT source_evaluation_id FROM strategy_scorecards WHERE scorecard_id=?", [scorecard_id]).fetchone()[0] == evaluation_id
    assert _run(runner, now) == (evaluation_id, scorecard_id)
    assert connection.execute("SELECT count(*),sum(total_equity) FROM sim_nav_daily WHERE account_id='acc'").fetchone() == before
    assert connection.execute("SELECT count(*) FROM sim_order_intents WHERE account_id='acc'").fetchone()[0] == 2
    assert connection.execute("SELECT spec_json FROM strategy_experiments WHERE experiment_id='experiment'").fetchone() == experiment_before
    assert connection.execute("SELECT metrics_json FROM strategy_experiment_results WHERE experiment_id='experiment'").fetchone() == result_before


def test_fingerprint_excludes_benchmark_but_includes_execution_fields():
    strategy = {"strategy_id": "s", "strategy_version": "v"}
    source = _payload(); benchmark_only = _payload('other'); execution_change = _payload(replay='changed')
    assert execution_fingerprint(source, strategy) == execution_fingerprint(benchmark_only, strategy)
    assert execution_fingerprint(source, strategy) != execution_fingerprint(execution_change, strategy)


def test_profile_hash_mismatch_and_execution_profile_mismatch_fail_closed():
    connection, now, runner = _runner_fixture()
    connection.execute("UPDATE competition_profiles SET profile_sha256='wrong' WHERE competition_profile_id='source'")
    with pytest.raises(ValueError, match='hash mismatch'): _run(runner, now)
    source_payload = _payload(market={"source": "fixture"})
    connection.execute("UPDATE competition_profiles SET profile_sha256=? WHERE competition_profile_id='source'", [sha256(json.dumps(source_payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()])
    connection.execute("UPDATE competition_profiles SET profile_json=?,profile_sha256=? WHERE competition_profile_id='evaluation'", [json.dumps(_payload(replay='different'), sort_keys=True, separators=(',', ':')), sha256(json.dumps(_payload(replay='different'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()])
    with pytest.raises(ValueError, match='changes replay execution'): _run(runner, now)


@pytest.mark.parametrize('bars', [_bars(((1, 100), (2, 101))), _bars(((1, 100), (2, 101), (2, 102), (3, 103)))])
def test_incomplete_or_duplicate_benchmark_fails_before_evaluation_write(bars):
    connection, now, runner = _runner_fixture()
    with pytest.raises(ValueError, match='benchmark dates'): _run(runner, now, bars)
    assert connection.execute("SELECT count(*) FROM strategy_evaluations").fetchone()[0] == 0


def test_duplicate_nav_and_pending_order_fail_closed():
    connection, now, runner = _runner_fixture()
    # The table's account/date primary key fail-closes duplicate NAV at storage.
    with pytest.raises(Exception):
        connection.execute("INSERT INTO sim_nav_daily VALUES ('acc','2026-09-02',1000,0,1000,1,0)")
    connection.execute("DELETE FROM sim_nav_daily WHERE account_id='acc' AND trade_date='2026-09-02'")
    with pytest.raises(ValueError, match='coverage'): _run(runner, now)
    connection.execute("INSERT INTO sim_nav_daily VALUES ('acc','2026-09-02',1010,0,1010,1.01,0)")
    connection.execute("INSERT INTO sim_order_intents VALUES ('pending',NULL,'acc','CCC','2026-09-03','BUY',100,'m','PENDING',NULL,?)", [now])
    with pytest.raises(ValueError, match='pending'): _run(runner, now)


def test_result_complete_resumes_scorecard_only_and_conflict_fails_closed():
    connection, now, runner = _runner_fixture()
    evaluation_id, scorecard_id = _run(runner, now)
    connection.execute("DELETE FROM strategy_scorecards WHERE scorecard_id=?", [scorecard_id])
    connection.execute("UPDATE strategy_evaluations SET status='RESULT_COMPLETE' WHERE evaluation_id=?", [evaluation_id])
    assert _run(runner, now) == (evaluation_id, connection.execute("SELECT scorecard_id FROM strategy_scorecards WHERE source_evaluation_id=?", [evaluation_id]).fetchone()[0])
    changed = _payload(replay='changed')
    text = json.dumps(changed, sort_keys=True, separators=(',', ':'))
    connection.execute("UPDATE competition_profiles SET profile_json=?,profile_sha256=? WHERE competition_profile_id='source'", [text, sha256(text.encode()).hexdigest()])
    with pytest.raises(ValueError, match='conflicts'): _run(runner, now)


def test_replay_validator_rejects_source_account_lineage():
    connection, _, _ = _runner_fixture()
    with pytest.raises(ValueError, match='account lineage'):
        validate_replay(connection, 'other', '2026-09-01', '2026-09-03', experiment_id='experiment')
