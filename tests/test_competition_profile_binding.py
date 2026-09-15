import copy
from pathlib import Path
import duckdb
import pytest

from quant_core.competition_profile_binding import complete_profile, semantic_hash
from quant_core.strategy_research import baseline_strategy_spec, pure_momentum_strategy_spec, kdj_manual_strategy_spec, macd_manual_strategy_spec


def _replay():
    return {"initial_cash":"1000000","candidate_top_n":5,"portfolio":{"method":"FIXED_SHARES","shares_per_order":100,"max_positions":None,"cash_reserve":"0"},"decision_timing":"T_close","entry_timing":"T_plus_1_open","rebalance_semantics":"daily","exit_rule":{"version":"v1","stop_loss_rate":".1","take_profit_min_rate":".15","trailing_drawdown_rate":".05","max_holding_days":60,"signal_timing":"close"},"fee_model":{"version":"v1","commission_rate":".00025","minimum_commission":"5","stamp_duty_rate":".0005","transfer_fee_rate":".00001","slippage_rate":".001"},"open_gap_gate":{"version":"v1","max_gap_up":".03","max_gap_down":"-.04"},"t_plus_one":"T1","suspension_handling":"defer_sell","price_limit_handling":"reject","missing_data_policy":"fail_closed","risk_overlay":{"version":"none"}}

def _profile(replay=None, universe=None, market=None, benchmark=None, methodology=None):
    return complete_profile(universe or {"snapshot_set_hash":"u","rule_version":"v","rule_json":{},"listing_reference":{"hash":"l"},"st_evidence":{"dataset_hash":"s"},"calendar_reference":{"hash":"c"}}, market or {"snapshot_set_hash":"m","bar_content_hash":"b"}, benchmark or {"dataset_hash":"z"}, replay or _replay(), methodology or {"evaluation_stage":"BACKTEST"})

def test_identical_profile_is_deterministic_and_ignores_nonsemantic_external_values():
    assert semantic_hash(_profile()) == semantic_hash(_profile())
    assert semantic_hash({"b":2,"a":1}) == semantic_hash({"a":1,"b":2})

@pytest.mark.parametrize("section,path", [
 ("universe",("snapshot_set_hash",)),("universe",("rule_version",)),("universe",("rule_json",)),("universe",("listing_reference","hash")),("universe",("st_evidence","dataset_hash")),("universe",("calendar_reference","hash")),
 ("market",("snapshot_set_hash",)),("market",("bar_content_hash",)),("benchmark",("dataset_hash",)),
])
def test_binding_mutations_change_profile_hash(section,path):
    base=_profile(); changed=copy.deepcopy(base)
    key={"universe":"universe_binding","market":"market_data_binding","benchmark":"benchmark_binding"}[section]
    target=changed[key]
    for part in path[:-1]: target=target[part]
    target[path[-1]]="changed"
    assert semantic_hash(base)!=semantic_hash(changed)

@pytest.mark.parametrize("path,value", [
 (("initial_cash",),"2"),(("candidate_top_n",),6),(("portfolio","shares_per_order"),200),(("portfolio","max_positions"),3),(("portfolio","cash_reserve"),".1"),(("entry_timing",),"open"),(("rebalance_semantics",),"weekly"),(("exit_rule","stop_loss_rate"),".2"),(("exit_rule","trailing_drawdown_rate"),".1"),(("exit_rule","max_holding_days"),61),(("open_gap_gate","max_gap_up"),".02"),(("fee_model","commission_rate"),".01"),(("fee_model","minimum_commission"),"6"),(("fee_model","stamp_duty_rate"),".001"),(("fee_model","transfer_fee_rate"),".1"),(("fee_model","slippage_rate"),".2"),(("t_plus_one",),"T0"),(("suspension_handling",),"reject"),(("price_limit_handling",),"other"),(("missing_data_policy",),"allow"),(("risk_overlay","version"),"guard"),
])
def test_replay_mutations_change_hash(path,value):
    a=_profile(); r=_replay(); target=r
    for part in path[:-1]: target=target[part]
    target[path[-1]]=value
    assert semantic_hash(a)!=semantic_hash(_profile(replay=r))

@pytest.mark.parametrize("mutate", [lambda r:r.update(portfolio={}),lambda r:r["fee_model"].pop("slippage_rate"),lambda r:r["exit_rule"].update(stop_loss_rate="2"),lambda r:r["portfolio"].update(shares_per_order=99)])
def test_replay_validation_fails_closed(mutate):
    r=_replay(); mutate(r)
    with pytest.raises(ValueError): _profile(replay=r)

def test_strategy_specs_do_not_enter_profile_and_are_distinct_identities():
    hashes={semantic_hash(_profile()) for _ in (baseline_strategy_spec(__import__('decimal').Decimal('1'),5),pure_momentum_strategy_spec(5),kdj_manual_strategy_spec(5),macd_manual_strategy_spec(5))}
    assert hashes=={semantic_hash(_profile())}
    specs=[baseline_strategy_spec(__import__('decimal').Decimal('1'),5),pure_momentum_strategy_spec(5),kdj_manual_strategy_spec(5),macd_manual_strategy_spec(5)]
    assert len({(s.strategy_id,s.strategy_version) for s in specs})==4

def test_method_specific_portfolio_validation_and_hash():
    target=_replay(); target["portfolio"]={"method":"FIXED_TARGET_NOTIONAL","max_positions":5,"cash_reserve":"0","target_notional_per_position":"200000","lot_size":100,"rounding":"FLOOR","insufficient_for_one_lot":"SKIP","no_leverage":True,"sizing_price_basis":"DECISION_CLOSE","actual_execution_notional_may_differ_due_to_next_open":True}
    assert semantic_hash(_profile(replay=target)) != semantic_hash(_profile())
    for field in ("target_notional_per_position","lot_size","rounding","insufficient_for_one_lot","no_leverage"):
        bad=copy.deepcopy(target); bad["portfolio"].pop(field)
        with pytest.raises(ValueError): _profile(replay=bad)
    bad=copy.deepcopy(target); bad["portfolio"]["shares_per_order"]=None
    with pytest.raises(ValueError): _profile(replay=bad)
    bad=_replay(); bad["portfolio"].pop("shares_per_order")
    with pytest.raises(ValueError): _profile(replay=bad)
    bad=_replay(); bad["portfolio"]["method"]="UNKNOWN"
    with pytest.raises(ValueError): _profile(replay=bad)

def test_stage_isolation_and_schema_initialization_is_idempotent(tmp_path):
    with pytest.raises(ValueError): _profile(methodology={"evaluation_stage":"SHADOW"})
    db=tmp_path/'x.duckdb'; con=duckdb.connect(str(db)); schema=Path('sql/schema.sql').read_text(); con.execute(schema); con.execute("INSERT INTO sim_accounts VALUES ('a','n','CNY',1,'r','f','ACTIVE',current_timestamp)"); con.execute(schema)
    assert con.execute("select count(*) from sim_accounts").fetchone()[0]==1
    assert con.execute("select count(*) from information_schema.tables where table_name='competition_profiles'").fetchone()[0]==1
