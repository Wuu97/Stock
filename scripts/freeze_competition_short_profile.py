"""Freeze a date-sliced profile with unchanged V4 execution assumptions."""
import argparse, json
from datetime import date, datetime, timezone
from quant_core.competition_profile_binding import market_binding, universe_binding
from quant_core.database import writer_connection
from quant_core.derived_evaluation import load_frozen_profile
from quant_core.strategy_scorecard import CompetitionProfile, StrategyScorecardStore

def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--parent-id',required=True); p.add_argument('--parent-version',required=True); p.add_argument('--profile-id',required=True); p.add_argument('--profile-version',required=True); p.add_argument('--start-date',required=True); p.add_argument('--end-date',required=True); p.add_argument('--calendar-path',required=True); a=p.parse_args()
 start,end=date.fromisoformat(a.start_date),date.fromisoformat(a.end_date)
 with writer_connection(a.db) as c:
  parent,parent_hash=load_frozen_profile(c,a.parent_id,a.parent_version); payload=json.loads(json.dumps(parent))
  payload['market_data_binding']=market_binding(c,parent['market_data_binding']['source'],start,end)
  payload['universe_binding']=universe_binding(c,parent['universe_binding']['group_id'],start,end,a.calendar_path)
  payload['benchmark_binding']['date_range']=[str(start),str(end)]; payload['benchmark_binding']['observation_count']=60
  payload['parent_profile']={'id':a.parent_id,'version':a.parent_version,'hash':parent_hash}
  digest=StrategyScorecardStore(c).ensure_profile(CompetitionProfile(a.profile_id,a.profile_version,payload),datetime.now(timezone.utc))
  print(json.dumps({'profile_hash':digest,'parent_hash':parent_hash},ensure_ascii=False))
if __name__=='__main__': main()
