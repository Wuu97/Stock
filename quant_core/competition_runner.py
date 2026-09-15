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
from .strategy_research import pure_momentum_strategy_spec, kdj_manual_strategy_spec, macd_manual_strategy_spec
from .trading_status import TradingStatusStore
from .universe import UniverseService
from .backtest import BacktestConfig, replay_daily_strategy
from .matching import OpenGapPolicy
from .models import FeeModel
from .portfolio import PortfolioPolicy
from .risk import ExitRule
from .derived_evaluation import validate_benchmark_dates
from .strategy_scorecard import StrategyScorecardStore

_STRATEGIES={'pure_momentum_v1':pure_momentum_strategy_spec,'kdj_manual_v1':kdj_manual_strategy_spec,'macd_manual_v1':macd_manual_strategy_spec}

def preflight(connection, profile_id, profile_version, strategy_id):
    if strategy_id not in _STRATEGIES: raise ValueError('unknown competition strategy')
    profile,digest=load_frozen_profile(connection,profile_id,profile_version)
    engine=profile.get('engine_binding'); market=profile['market_data_binding']; replay=profile['replay_assumptions']; universe=profile['universe_binding']
    start,end=map(__import__('datetime').date.fromisoformat,market['date_range'])
    validate_engine_binding(connection,engine,start,end)
    if market_binding(connection,market['source'],start,end)!=market: raise ValueError('frozen market binding evidence mismatch')
    strategy=_STRATEGIES[strategy_id](int(replay['candidate_top_n']))
    snapshots=[r[0] for r in connection.execute('SELECT market_snapshot_id FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date,market_snapshot_id',[market['source'],start,end]).fetchall()]
    groups=UniverseService(connection).members_by_trade_date(universe['group_id'],start,end)
    if not groups: raise ValueError('frozen universe evidence is missing')
    return profile,digest,strategy,start,end,snapshots,groups

def run(connection, *, account_id, profile_id, profile_version, strategy_id, benchmark_bars, created_at):
    """Execute only after frozen-profile preflight; StrategySpec is the sole variable."""
    if connection.execute('SELECT 1 FROM sim_accounts WHERE account_id=?',[account_id]).fetchone(): raise ValueError('competition account must be fresh')
    profile,digest,strategy,start,end,snapshots,groups=preflight(connection,profile_id,profile_version,strategy_id)
    replay, engine=profile['replay_assumptions'],profile['engine_binding']; p=replay['portfolio']; fee=replay['fee_model']; ex=replay['exit_rule']; gap=replay['open_gap_gate']
    policy=PortfolioPolicy.fixed_target_notional(Decimal(p['target_notional_per_position']),int(p['max_positions']),int(p['lot_size']))
    costs=FeeModel(fee['version'],Decimal(fee['commission_rate']),Decimal(fee['minimum_commission']),Decimal(str(fee['stamp_duty_rate']).split('_')[0]),Decimal(fee['transfer_fee_rate']),Decimal(fee['slippage_rate']))
    exits=ExitRule(Decimal(ex['stop_loss_rate']),Decimal(ex['take_profit_min_rate']),Decimal(ex['trailing_drawdown_rate']),int(ex['max_holding_days']))
    open_gap=OpenGapPolicy(Decimal(gap['max_gap_up']),Decimal(gap['max_gap_down']))
    status=TradingStatusStore(connection).suspended_tickers_by_snapshot_ids(engine['trading_status_snapshot_ids'],start,end)
    tickers=set().union(*groups.values()); bars=MarketDataStore(connection).load_bars_many_for_tickers(snapshots,tickers)
    days=sorted({b.trade_date for b in bars}); validate_benchmark_dates(benchmark_bars,days)
    spec=ExperimentSpec(strategy,policy,exits,costs.version,start,end,tuple(snapshots),'PIT_GROUP:'+profile['universe_binding']['group_id'],profile['benchmark_binding']['identifier'],'NORMALIZED_DATASET_SHA256:'+profile['benchmark_binding']['dataset_hash'],Decimal(replay['initial_cash']),open_gap)
    fingerprint=execution_fingerprint(profile,strategy.__dict__)
    eid=str(uuid4()); SettlementService(connection).create_account(account_id,'Competition '+eid[:8],Decimal(replay['initial_cash']),start)
    ExperimentStore(connection).create(eid,account_id,spec,created_at)
    connection.execute('INSERT INTO strategy_experiment_competition_lineage VALUES (?,?,?,?,?,?)',[eid,profile_id,profile_version,digest,fingerprint,created_at])
    replay=replay_daily_strategy(connection,bars,days,BacktestConfig(account_id,start,end,int(p['lot_size']),int(engine['feature_lookback_days']),int(replay['candidate_top_n']),Decimal('1'),strategy,policy,open_gap),costs,exits,universe_by_date=groups,suspended_tickers_by_date=status)
    metrics=calculate_metrics(connection,account_id,benchmark_bars); ExperimentStore(connection).store_result(eid,metrics,created_at)
    scorecard_id=StrategyScorecardStore(connection).store_frozen_experiment_scorecard(eid,profile_id,profile_version,created_at)
    return eid,scorecard_id,replay,metrics
