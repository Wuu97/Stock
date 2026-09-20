from datetime import date
from decimal import Decimal

import pytest

import quant_core.competition_runner as runner
from quant_core.experiments import BenchmarkClose
from quant_core.strategy_research import baseline_strategy_spec, pure_momentum_strategy_spec
from tests.test_strategy_scorecard import _fixture


def _preflight(connection, profile_id, profile_version, strategy_id):
    profile={"universe_binding":{"group_id":"fixture"},"market_data_binding":{},"benchmark_binding":{"identifier":"BENCH","dataset_hash":"hash"},"engine_binding":{"feature_lookback_days":20,"trading_status_snapshot_ids":["s"]},"replay_assumptions":{"initial_cash":"1000","candidate_top_n":5,"portfolio":{"target_notional_per_position":"200","max_positions":5,"lot_size":100},"fee_model":{"version":"cost","commission_rate":"0","minimum_commission":"0","stamp_duty_rate":"0.0005_sell_only","transfer_fee_rate":"0","slippage_rate":"0"},"exit_rule":{"stop_loss_rate":"0.10","take_profit_min_rate":"0.15","trailing_drawdown_rate":"0.05","max_holding_days":60},"open_gap_gate":{"max_gap_up":"0.03","max_gap_down":"-0.04"}}}
    return profile,'profile-hash',pure_momentum_strategy_spec(5),date(2026,9,1),date(2026,9,3),['snapshot'],{date(2026,9,d):{'AAA'} for d in (1,2,3)}


@pytest.mark.parametrize('stage', ['replay','metrics','scorecard'])
def test_competition_failure_cleanup_leaves_no_run_facts(monkeypatch, stage):
    connection, now = _fixture()
    tables=('sim_accounts','strategy_experiments','strategy_experiment_results','strategy_experiment_competition_lineage','sim_nav_daily','sim_positions_daily','sim_position_lots','sim_order_intents','sim_executions','ledger_journal_entries','sim_exit_signals','sim_suspension_valuation_events','sim_dividend_entitlements','sim_lot_disposal_events','sim_lot_adjustment_events')
    before={table:connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}
    connection.execute("INSERT INTO competition_profiles VALUES ('p','v4','{}','profile-hash',?)", [now])
    monkeypatch.setattr(runner, 'preflight', _preflight)
    monkeypatch.setattr(runner.TradingStatusStore, 'suspended_tickers_by_snapshot_ids', lambda *a,**k:{})
    monkeypatch.setattr(runner.MarketDataStore, 'load_bars_many_for_tickers', lambda *a,**k: [])
    monkeypatch.setattr(runner, 'validate_benchmark_dates', lambda bars, days: bars)
    if stage == 'replay': monkeypatch.setattr(runner, 'replay_daily_strategy', lambda *a,**k: (_ for _ in ()).throw(RuntimeError('replay')))
    else: monkeypatch.setattr(runner, 'replay_daily_strategy', lambda *a,**k: {})
    if stage == 'metrics': monkeypatch.setattr(runner, 'calculate_metrics', lambda *a,**k: (_ for _ in ()).throw(RuntimeError('metrics')))
    else: monkeypatch.setattr(runner, 'calculate_metrics', lambda *a,**k: {'nav_observations': 3})
    if stage == 'scorecard': monkeypatch.setattr(runner.StrategyScorecardStore, 'store_frozen_experiment_scorecard', lambda *a,**k: (_ for _ in ()).throw(RuntimeError('scorecard')))
    with pytest.raises(RuntimeError, match=stage):
        runner.run(connection, account_id='competition-test', profile_id='p', profile_version='v4', strategy_id='pure_momentum_v1', benchmark_bars=[BenchmarkClose(date(2026,9,d),Decimal('1')) for d in (1,2,3)], created_at=now)
    assert {table:connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables} == before


def test_non_volume_multiplier_is_not_read_by_explicit_pure_strategy():
    spec=pure_momentum_strategy_spec(5)
    assert spec.provider_type == 'PURE_MOMENTUM'
    assert 'volume_multiple' not in spec.parameters
    assert runner.NON_VOLUME_STRATEGY_MULTIPLIER == Decimal('1')


def test_competition_runner_registers_the_existing_baseline_without_changing_its_rule():
    spec = runner._STRATEGIES['historical_momentum_v1'](5)
    assert spec == baseline_strategy_spec(Decimal('1.5'), 5)
    assert spec.strategy_id == 'historical_momentum_v1'
