import argparse,json
from datetime import date,datetime,timezone
from hashlib import sha256
from pathlib import Path
from quant_core.competition_profile_binding import market_binding
from quant_core.database import writer_connection
from quant_core.derived_evaluation import load_frozen_profile
from quant_core.strategy_scorecard import CompetitionProfile,StrategyScorecardStore

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--parent-id',required=True);p.add_argument('--parent-version',required=True);p.add_argument('--profile-id',required=True);p.add_argument('--profile-version',required=True);p.add_argument('--dataset',required=True);p.add_argument('--dataset-manifest',required=True);p.add_argument('--schedule',required=True);p.add_argument('--predictions',required=True);p.add_argument('--prediction-manifest',required=True);p.add_argument('--benchmark',required=True);a=p.parse_args()
 with writer_connection(a.db) as c:
  parent,parent_hash=load_frozen_profile(c,a.parent_id,a.parent_version); payload=json.loads(json.dumps(parent)); start,end=date(2023,9,12),date(2026,9,11)
  warm=c.execute("select distinct trade_date from market_data_snapshots where source_channel=? and trade_date < ? order by trade_date desc limit 20",[parent['market_data_binding']['source'],start]).fetchall(); warm_days=[x[0] for x in warm]
  payload['feature_warmup_market_binding']=market_binding(c,parent['market_data_binding']['source'],min(warm_days),max(warm_days)); payload['parent_profile']={'id':a.parent_id,'version':a.parent_version,'hash':parent_hash}
  payload['ridge_binding']={'dataset_sha256':sha256(Path(a.dataset).read_bytes()).hexdigest(),'dataset_manifest_sha256':sha256(Path(a.dataset_manifest).read_bytes()).hexdigest(),'schedule_sha256':sha256(Path(a.schedule).read_bytes()).hexdigest(),'prediction_artifact_sha256':sha256(Path(a.predictions).read_bytes()).hexdigest(),'prediction_manifest_sha256':sha256(Path(a.prediction_manifest).read_bytes()).hexdigest(),'benchmark_artifact_sha256':sha256(Path(a.benchmark).read_bytes()).hexdigest(),'training_window_days':480,'prediction_window_days':60,'ridge_alpha':1.0}
  digest=StrategyScorecardStore(c).ensure_profile(CompetitionProfile(a.profile_id,a.profile_version,payload),datetime.now(timezone.utc));print(json.dumps({'profile_hash':digest,'parent_hash':parent_hash}))
if __name__=='__main__':main()
