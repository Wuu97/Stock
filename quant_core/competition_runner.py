"""Strict orchestration layer; delegates all simulation to replay_daily_strategy."""
import json
from datetime import datetime
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

def run(*args, **kwargs):
    """Reserved execution entrypoint: callers must pass only operational inputs.

    It intentionally has no CLI override parameters for portfolio, fee, exit,
    universe, market range, timing, or engine evidence.
    """
    raise NotImplementedError('competition replay execution is enabled only after lineage persistence is wired')
