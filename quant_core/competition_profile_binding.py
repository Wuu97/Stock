"""Deterministic, strategy-neutral bindings for a Competition Profile."""
import json
from hashlib import sha256
from pathlib import Path


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def semantic_hash(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def universe_binding(connection, group_name, start, end, calendar_path):
    rows = connection.execute("""SELECT u.as_of_trade_date,u.universe_snapshot_id,u.market_cap_snapshot_id,u.rule_version,u.rule_json,
      u.listing_snapshot_id,u.st_backfill_run_id,m.ticker,m.rank_order,m.total_market_cap,m.momentum_return
      FROM universe_snapshots u LEFT JOIN universe_members m ON m.universe_snapshot_id=u.universe_snapshot_id
      WHERE u.group_name=? AND u.as_of_trade_date BETWEEN ? AND ? ORDER BY 1,8""", [group_name,start,end]).fetchall()
    if not rows or any(r[5] is None or r[6] is None for r in rows): raise ValueError("universe snapshots have incomplete evidence lineage")
    rule_versions, rules = {r[3] for r in rows}, {r[4] for r in rows}
    if len(rule_versions)!=1 or len(rules)!=1: raise ValueError("universe rule is not semantically uniform")
    listing_id, st_id = rows[0][5], rows[0][6]
    listing = connection.execute("SELECT raw_artifact_sha256 FROM security_listing_snapshots WHERE listing_snapshot_id=?",[listing_id]).fetchone()
    st = connection.execute("SELECT source_channel,start_trade_date,end_trade_date,source_policy_version FROM st_history_backfill_runs WHERE backfill_run_id=?",[st_id]).fetchone()
    if not listing or not st: raise ValueError("universe evidence reference is missing")
    calendar = json.loads(Path(calendar_path).read_text())
    st_rows=connection.execute("SELECT trade_date,ticker,is_st FROM st_history_daily WHERE backfill_run_id=? AND trade_date BETWEEN ? AND ? ORDER BY 1,2",[st_id,start,end]).fetchall()
    if not st_rows: raise ValueError("ST evidence dataset is empty")
    return {"group_id":group_name,"date_range":[str(start),str(end)],"rule_version":next(iter(rule_versions)),"rule_json":json.loads(next(iter(rules))),
      "snapshot_set_hash":semantic_hash(rows),"listing_reference":{"id":listing_id,"hash":listing[0]},
      "st_evidence":{"run_id":st_id,"metadata_hash":semantic_hash(st),"dataset_hash":semantic_hash(st_rows)},"calendar_reference":{"id":Path(calendar_path).name,"hash":semantic_hash(calendar)}}


def market_binding(connection, source, start, end):
    rows=connection.execute("SELECT market_snapshot_id,trade_date,source_channel,source_published_at,manifest_sha256 FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date,market_snapshot_id",[source,start,end]).fetchall()
    if not rows: raise ValueError("market snapshot set is empty")
    bars=connection.execute("""SELECT b.market_snapshot_id,b.trade_date,b.ticker,b.open,b.high,b.low,b.close,b.volume,b.amount,b.limit_up,b.limit_down,b.status
      FROM daily_bars b JOIN market_data_snapshots s ON s.market_snapshot_id=b.market_snapshot_id
      WHERE s.source_channel=? AND s.trade_date BETWEEN ? AND ? ORDER BY 1,2,3""",[source,start,end]).fetchall()
    if not bars: raise ValueError("market bar dataset is empty")
    return {"source":source,"date_range":[str(start),str(end)],"snapshot_set_hash":semantic_hash(rows),"bar_content_hash":semantic_hash(bars),"snapshot_count":len(rows)}


def benchmark_binding(identifier, source, version, path):
    if not all((identifier,source,version,path)): raise ValueError("benchmark binding is incomplete")
    raw=Path(path).read_bytes()
    return {"identifier":identifier,"source":source,"version":version,"dataset_hash":sha256(raw).hexdigest()}


def complete_profile(universe, market, benchmark, replay, methodology):
    required={"initial_cash","candidate_top_n","portfolio","decision_timing","entry_timing","rebalance_semantics","exit_rule","fee_model","open_gap_gate","t_plus_one","suspension_handling","price_limit_handling","missing_data_policy","risk_overlay"}
    missing=required-set(replay)
    if missing: raise ValueError("missing mandatory replay assumptions: "+",".join(sorted(missing)))
    nested={"exit_rule":{"version","stop_loss_rate","take_profit_min_rate","trailing_drawdown_rate","max_holding_days","signal_timing"},"fee_model":{"version","commission_rate","minimum_commission","stamp_duty_rate","transfer_fee_rate","slippage_rate"},"open_gap_gate":{"version","max_gap_up","max_gap_down"},"risk_overlay":{"version"}}
    for name, fields in nested.items():
        if not isinstance(replay[name],dict) or fields-set(replay[name]): raise ValueError("incomplete replay binding: "+name)
    portfolio=replay["portfolio"]
    if not isinstance(portfolio,dict) or {"method","max_positions","cash_reserve"}-set(portfolio): raise ValueError("incomplete replay binding: portfolio")
    method=portfolio["method"]
    if method=="FIXED_SHARES":
        if "shares_per_order" not in portfolio or portfolio["shares_per_order"] is None or "target_notional_per_position" in portfolio: raise ValueError("invalid fixed-shares portfolio binding")
        lot=portfolio["shares_per_order"]
    elif method=="FIXED_TARGET_NOTIONAL":
        fields={"target_notional_per_position","lot_size","rounding","insufficient_for_one_lot","no_leverage"}
        if fields-set(portfolio) or "shares_per_order" in portfolio: raise ValueError("invalid fixed-target-notional portfolio binding")
        if float(portfolio["target_notional_per_position"])<=0 or portfolio["rounding"] not in {"FLOOR"} or portfolio["insufficient_for_one_lot"] not in {"SKIP"} or not isinstance(portfolio["no_leverage"],bool) or not portfolio["no_leverage"]: raise ValueError("invalid fixed-target-notional semantics")
        lot=portfolio["lot_size"]
    else: raise ValueError("invalid portfolio method")
    if int(replay["candidate_top_n"])<1 or int(lot)<100 or int(lot)%100: raise ValueError("invalid replay sizing")
    for value in ("stop_loss_rate","take_profit_min_rate","trailing_drawdown_rate"):
        if not 0<float(replay["exit_rule"][value])<1: raise ValueError("invalid exit rate")
    if float(replay["fee_model"]["commission_rate"])<0 or float(replay["fee_model"]["slippage_rate"])<0: raise ValueError("invalid fee rate")
    if methodology.get("evaluation_stage")!="BACKTEST": raise ValueError("Competition Profile V1 dry-run is BACKTEST only")
    return {"profile_schema_version":"competition_profile_v1","universe_binding":universe,"market_data_binding":market,"benchmark_binding":benchmark,"replay_assumptions":replay,"evaluation_methodology":methodology}
