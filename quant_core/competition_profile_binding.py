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
    return {"group_id":group_name,"date_range":[str(start),str(end)],"rule_version":next(iter(rule_versions)),"rule_json":json.loads(next(iter(rules))),
      "snapshot_set_hash":semantic_hash(rows),"listing_reference":{"id":listing_id,"hash":listing[0]},
      "st_evidence":{"run_id":st_id,"hash":semantic_hash(st)},"calendar_reference":{"id":Path(calendar_path).name,"hash":semantic_hash(calendar)}}


def market_binding(connection, source, start, end):
    rows=connection.execute("SELECT market_snapshot_id,trade_date,source_channel,source_published_at,manifest_sha256 FROM market_data_snapshots WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date,market_snapshot_id",[source,start,end]).fetchall()
    if not rows: raise ValueError("market snapshot set is empty")
    return {"source":source,"date_range":[str(start),str(end)],"snapshot_set_hash":semantic_hash(rows),"snapshot_count":len(rows)}


def benchmark_binding(identifier, source, version, path):
    if not all((identifier,source,version,path)): raise ValueError("benchmark binding is incomplete")
    raw=Path(path).read_bytes()
    return {"identifier":identifier,"source":source,"version":version,"dataset_hash":sha256(raw).hexdigest()}


def complete_profile(universe, market, benchmark, replay, methodology):
    required={"initial_cash","candidate_top_n","portfolio","decision_timing","entry_timing","rebalance_semantics","exit_rule","fee_model","t_plus_one","suspension_handling","price_limit_handling","missing_data_policy","risk_overlay"}
    missing=required-set(replay)
    if missing: raise ValueError("missing mandatory replay assumptions: "+",".join(sorted(missing)))
    if methodology.get("evaluation_stage")!="BACKTEST": raise ValueError("Competition Profile V1 dry-run is BACKTEST only")
    return {"profile_schema_version":"competition_profile_v1","universe_binding":universe,"market_data_binding":market,"benchmark_binding":benchmark,"replay_assumptions":replay,"evaluation_methodology":methodology}
