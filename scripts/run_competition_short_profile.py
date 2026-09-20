import argparse,csv,json
from datetime import date,datetime,timezone
from decimal import Decimal
from pathlib import Path
from quant_core.competition_runner import run
from quant_core.database import writer_connection
from quant_core.experiments import BenchmarkClose
from quant_core.ridge_schedule import RidgeModelSchedule,ScheduledMLRidgeScoreProvider
from quant_core.strategy_research import ML_RIDGE_PROVIDER_TYPE,StrategySpec

def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--profile-id',required=True); p.add_argument('--profile-version',required=True); p.add_argument('--strategy',required=True); p.add_argument('--account-id',required=True); p.add_argument('--benchmark-csv',required=True); p.add_argument('--schedule'); p.add_argument('--output',required=True); a=p.parse_args()
 with writer_connection(a.db,transaction=False) as c:
  row=c.execute('select profile_json from competition_profiles where competition_profile_id=? and competition_profile_version=?',[a.profile_id,a.profile_version]).fetchone(); profile=json.loads(row[0]); start,end=map(date.fromisoformat,profile['market_data_binding']['date_range'])
  benchmark=[BenchmarkClose(date.fromisoformat(r['trade_date']),Decimal(r['adj_close'])) for r in csv.DictReader(open(a.benchmark_csv)) if start<=date.fromisoformat(r['trade_date'])<=end]
  kw={}
  if a.schedule:
   schedule=RidgeModelSchedule.load(Path(a.schedule)); spec=StrategySpec('ml_ridge_v4_short_validation_v1','ridge_v1',ML_RIDGE_PROVIDER_TYPE,json.dumps({'top_n':5,'schedule_sha256':schedule.artifact_sha256},sort_keys=True)); kw={'strategy_spec':spec,'score_provider':ScheduledMLRidgeScoreProvider(spec,schedule)}
  result=run(c,account_id=a.account_id,profile_id=a.profile_id,profile_version=a.profile_version,strategy_id=a.strategy,benchmark_bars=benchmark,created_at=datetime.now(timezone.utc),**kw)
 Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps({'experiment_id':result[0],'scorecard_id':result[1],'replay':result[2],'metrics':result[3]},default=str,indent=2)+'\n'); print(json.dumps({'experiment_id':result[0],'scorecard_id':result[1]}))
if __name__=='__main__': main()
