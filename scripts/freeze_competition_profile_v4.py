"""Freeze v4 by adding only missing engine evidence to frozen v3."""
import argparse, json
from datetime import datetime, timezone
from quant_core.database import writer_connection
from quant_core.strategy_scorecard import CompetitionProfile, StrategyScorecardStore
from quant_core.derived_evaluation import load_frozen_profile
from quant_core.competition_engine_binding import canonical_snapshot_set, validate_engine_binding

IDS=['tushare_suspend_d_c9978277416b9b45d8927243','tushare_suspend_d_e2972aeddd25c629184f111c','tushare_suspend_d_c2ac6646e84a272f81b1dcfe']
def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True); a=p.parse_args()
 with writer_connection(a.db) as c:
  source, source_hash=load_frozen_profile(c,'large_cap_top5_medium_term','v3')
  rows=c.execute("SELECT trading_status_snapshot_id,source_channel,start_trade_date,end_trade_date,raw_artifact_sha256 FROM trading_status_snapshots WHERE trading_status_snapshot_id IN (?,?,?)",IDS).fetchall()
  digest,_=canonical_snapshot_set(rows)
  payload=json.loads(json.dumps(source)); payload['engine_binding']={'feature_lookback_days':20,'trading_status_source':'tushare_suspend_d','trading_status_snapshot_ids':sorted(IDS),'trading_status_dataset_hash':digest}
  start,end=map(__import__('datetime').date.fromisoformat,payload['market_data_binding']['date_range']); validate_engine_binding(c,payload['engine_binding'],start,end)
  h=StrategyScorecardStore(c).ensure_profile(CompetitionProfile('large_cap_top5_medium_term','v4',payload),datetime.now(timezone.utc))
  print(json.dumps({'v3_hash':source_hash,'v4_hash':h,'engine_binding':payload['engine_binding']},sort_keys=True))
if __name__=='__main__': main()
