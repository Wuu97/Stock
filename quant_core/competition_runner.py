"""Strict orchestration layer; delegates all simulation to replay_daily_strategy."""
import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from hashlib import sha256

from .competition_engine_binding import validate_engine_binding
from .competition_profile_binding import market_binding
from .derived_evaluation import execution_fingerprint, load_frozen_profile
from .experiments import ExperimentSpec, ExperimentStore, calculate_metrics
from .market_data import MarketDataStore
from .settlement import SettlementService
from .strategy_research import baseline_strategy_spec, pure_momentum_strategy_spec, kdj_manual_strategy_spec, macd_manual_strategy_spec
from .trading_status import TradingStatusStore
from .universe import UniverseService
from .backtest import BacktestConfig, replay_daily_strategy
from .matching import OpenGapPolicy
from .models import FeeModel
from .portfolio import PortfolioPolicy
from .risk import ExitRule
from .derived_evaluation import validate_benchmark_dates
from .strategy_scorecard import StrategyScorecardStore

_STRATEGIES={
    'historical_momentum_v1': lambda top_n: baseline_strategy_spec(Decimal('1.5'), top_n),
    'pure_momentum_v1': pure_momentum_strategy_spec,
    'kdj_manual_v1': kdj_manual_strategy_spec,
    'macd_manual_v1': macd_manual_strategy_spec,
}
NON_VOLUME_STRATEGY_MULTIPLIER=Decimal('1')  # ignored whenever an explicit non-baseline StrategySpec is supplied

def _sell_only_stamp_rate(value):
    if not isinstance(value,str) or not value.endswith('_sell_only') or value.count('_') != 2: raise ValueError('frozen stamp duty must use <decimal>_sell_only')
    rate=Decimal(value.removesuffix('_sell_only'))
    if rate < 0: raise ValueError('frozen stamp duty cannot be negative')
    return rate

def _cleanup_failed_run(connection, account_id, experiment_id):
    connection.execute('DELETE FROM strategy_experiment_competition_lineage WHERE experiment_id=?',[experiment_id])
    connection.execute('DELETE FROM strategy_experiment_results WHERE experiment_id=?',[experiment_id])
    connection.execute('DELETE FROM strategy_experiments WHERE experiment_id=?',[experiment_id])
    lot_ids=[r[0] for r in connection.execute('SELECT lot_id FROM sim_position_lots WHERE account_id=?',[account_id]).fetchall()]
    if lot_ids:
        marks=','.join('?' for _ in lot_ids)
        connection.execute('DELETE FROM sim_lot_disposal_events WHERE lot_id IN ('+marks+')',lot_ids)
        connection.execute('DELETE FROM sim_lot_adjustment_events WHERE lot_id IN ('+marks+')',lot_ids)
    for table in ('sim_dividend_entitlements','sim_exit_signals','sim_suspension_valuation_events','sim_positions_daily','sim_nav_daily','sim_executions','sim_order_intents','sim_position_lots','ledger_journal_entries','sim_accounts'):
        connection.execute(f'DELETE FROM {table} WHERE account_id=?',[account_id])

def preflight(connection, profile_id, profile_version, strategy_id, strategy=None):
    if strategy is None and strategy_id not in _STRATEGIES: raise ValueError('unknown competition strategy')
    profile,digest=load_frozen_profile(connection,profile_id,profile_version)
    engine=profile.get('engine_binding'); market=profile['market_data_binding']; replay=profile['replay_assumptions']; universe=profile['universe_binding']
    start,end=map(__import__('datetime').date.fromisoformat,market['date_range'])
    validate_engine_binding(connection,engine,start,end)
    if market_binding(connection,market['source'],start,end)!=market: raise ValueError('frozen market binding evidence mismatch')
    strategy=strategy or _STRATEGIES[strategy_id](int(replay['candidate_top_n']))
    snapshots=[r[0] for r in connection.execute('SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date,market_snapshot_id',[market['source'],start,end]).fetchall()]
    groups=UniverseService(connection).members_by_trade_date(universe['group_id'],start,end)
    if not groups: raise ValueError('frozen universe evidence is missing')
    return profile,digest,strategy,start,end,snapshots,groups

def run(connection, *, account_id, profile_id, profile_version, strategy_id, benchmark_bars, created_at, strategy_spec=None, score_provider=None):
    """Execute only after frozen-profile preflight; StrategySpec is the sole variable."""
    if connection.execute('SELECT 1 FROM sim_accounts WHERE account_id=?',[account_id]).fetchone(): raise ValueError('competition account must be fresh')
    profile,digest,strategy,start,end,snapshots,groups=(preflight(connection,profile_id,profile_version,strategy_id,strategy_spec)
                                                        if strategy_spec is not None else preflight(connection,profile_id,profile_version,strategy_id))
    replay, engine=profile['replay_assumptions'],profile['engine_binding']; p=replay['portfolio']; fee=replay['fee_model']; ex=replay['exit_rule']; gap=replay['open_gap_gate']
    policy=PortfolioPolicy.fixed_target_notional(Decimal(p['target_notional_per_position']),int(p['max_positions']),int(p['lot_size']))
    costs=FeeModel(fee['version'],Decimal(fee['commission_rate']),Decimal(fee['minimum_commission']),_sell_only_stamp_rate(fee['stamp_duty_rate']),Decimal(fee['transfer_fee_rate']),Decimal(fee['slippage_rate']))
    exits=ExitRule(Decimal(ex['stop_loss_rate']),Decimal(ex['take_profit_min_rate']),Decimal(ex['trailing_drawdown_rate']),int(ex['max_holding_days']))
    open_gap=OpenGapPolicy(Decimal(gap['max_gap_up']),Decimal(gap['max_gap_down']))
    status=TradingStatusStore(connection).suspended_tickers_by_snapshot_ids(engine['trading_status_snapshot_ids'],start,end)
    tickers=set().union(*groups.values()); bars=MarketDataStore(connection).load_bars_many_for_tickers(snapshots,tickers)
    days=sorted({b.trade_date for b in bars}); validate_benchmark_dates(benchmark_bars,days)
    spec=ExperimentSpec(strategy,policy,exits,costs.version,start,end,tuple(snapshots),'PIT_GROUP:'+profile['universe_binding']['group_id'],profile['benchmark_binding']['identifier'],'NORMALIZED_DATASET_SHA256:'+profile['benchmark_binding']['dataset_hash'],Decimal(replay['initial_cash']),open_gap)
    fingerprint=execution_fingerprint(profile,strategy.__dict__)
    eid=str(uuid4())
    try:
        SettlementService(connection).create_account(account_id,'Competition '+eid[:8],Decimal(replay['initial_cash']),start)
        ExperimentStore(connection).create(eid,account_id,spec,created_at)
        connection.execute('INSERT INTO strategy_experiment_competition_lineage VALUES (?,?,?,?,?,?)',[eid,profile_id,profile_version,digest,fingerprint,created_at])
        replay=replay_daily_strategy(connection,bars,days,BacktestConfig(account_id,start,end,int(p['lot_size']),int(engine['feature_lookback_days']),int(replay['candidate_top_n']),NON_VOLUME_STRATEGY_MULTIPLIER,strategy,policy,open_gap),costs,exits,universe_by_date=groups,score_provider=score_provider,suspended_tickers_by_date=status)
        metrics=calculate_metrics(connection,account_id,benchmark_bars); ExperimentStore(connection).store_result(eid,metrics,created_at)
        scorecard_id=StrategyScorecardStore(connection).store_frozen_experiment_scorecard(eid,profile_id,profile_version,created_at)
        return eid,scorecard_id,replay,metrics
    except Exception:
        _cleanup_failed_run(connection,account_id,eid); raise
