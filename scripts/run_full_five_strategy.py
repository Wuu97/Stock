import argparse,csv,json
from datetime import date,datetime,timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from quant_core.database import writer_connection
from quant_core.competition_runner import run
from quant_core.experiments import BenchmarkClose
from quant_core.strategy_research import StrategySpec,ML_RIDGE_PROVIDER_TYPE,FrozenPredictionArtifactScoreProvider
from quant_core.profile_bridge import validate_profile_bridge

def fh(p): return sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--profile-id',required=True);p.add_argument('--profile-version',required=True);p.add_argument('--benchmark',required=True);p.add_argument('--dataset',required=True);p.add_argument('--dataset-manifest',required=True);p.add_argument('--schedule',required=True);p.add_argument('--predictions',required=True);p.add_argument('--prediction-manifest',required=True);p.add_argument('--profile-bridge');p.add_argument('--output-dir',required=True);a=p.parse_args()
 expected={'dataset': '8538db88577fccae8c7ccc57c3a4313470b94dd613dc5a7cd7a04acfa3ef7bad','manifest':'0104f2f0fb867a5be984b016202d7d886a5c3b684bc37fd3a7f3c4fa12d16af4','schedule':'b4d6f02822ab5662c003b0776b16f954da9793849f7a6de7d5ea93fdf54f3060','predictions':'a1743a4dfb209dd0ba54fb2c378f5bf356f4b91954ca1089994c7991e7e562d8'}
 actual={'dataset':fh(a.dataset),'manifest':fh(a.dataset_manifest),'schedule':fh(a.schedule),'predictions':fh(a.predictions)}
 # Inputs are the frozen V2 artifacts supplied for this experiment.
 if actual!=expected: raise ValueError('frozen artifact hash mismatch: '+json.dumps({'expected':expected,'actual':actual}))
 pm=json.loads(Path(a.prediction_manifest).read_text());
 if pm.get('artifact_sha256')!=actual['predictions'] or pm.get('schedule_sha256')!=actual['schedule']: raise ValueError('prediction manifest lineage mismatch')
 preds={};
 for line in Path(a.predictions).read_text().splitlines():
  x=json.loads(line); key=(date.fromisoformat(x['prediction_trade_date']),x['ticker']);
  if key in preds: raise ValueError('duplicate prediction key')
  preds[key]=float(x['prediction_score'])
 out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
 with writer_connection(a.db,transaction=False) as c:
  profile_row=c.execute('select profile_json,profile_sha256 from competition_profiles where competition_profile_id=? and competition_profile_version=?',[a.profile_id,a.profile_version]).fetchone()
  if not profile_row: raise ValueError('execution profile is absent')
  profile,execution_hash=json.loads(profile_row[0]),profile_row[1]
  source_hash=pm.get('profile',{}).get('hash') or pm.get('profile_hash')
  if source_hash!=execution_hash and not a.profile_bridge: raise ValueError('source/execution profile mismatch requires an explicit hash-bound bridge')
  bridge=json.loads(Path(a.profile_bridge).read_text()) if a.profile_bridge else None
  validate_profile_bridge(bridge,source_hash,execution_hash,actual['predictions'],actual['schedule'])
  start,end=map(date.fromisoformat,profile['market_data_binding']['date_range']); bench=[BenchmarkClose(date.fromisoformat(r['trade_date']),Decimal(r['adj_close'])) for r in csv.DictReader(open(a.benchmark)) if start<=date.fromisoformat(r['trade_date'])<=end]
  specs=[('baseline','historical_momentum_v1',None),('kdj','kdj_manual_v1',None),('macd','macd_manual_v1',None),('pure','pure_momentum_v1',None)]
  ridge_spec=StrategySpec('ridge_v4_full_3y_v1','ridge_v1',ML_RIDGE_PROVIDER_TYPE,json.dumps({'top_n':5,'prediction_artifact_sha256':actual['predictions'],'schedule_sha256':actual['schedule']},sort_keys=True)); specs.append(('ridge','ridge_v4_full_3y_v1',ridge_spec))
  results=[]
  for label,sid,spec in specs:
   existing=out/f'{label}.json'
   if existing.exists():
    continue
   account='competition_v4_ridge_3y_'+label
   kw={}
   if spec is not None: kw={'strategy_spec':spec,'score_provider':FrozenPredictionArtifactScoreProvider(spec,preds)}
   result=run(c,account_id=account,profile_id=a.profile_id,profile_version=a.profile_version,strategy_id=sid,benchmark_bars=bench,created_at=datetime.now(timezone.utc),**kw)
   report={'experiment_id':result[0],'scorecard_id':result[1],'replay':result[2],'metrics':result[3],'account_id':account,'strategy':label,'profile_id':a.profile_id,'profile_version':a.profile_version,'frozen_hashes':actual}
   path=out/f'{label}.json';path.write_text(json.dumps(report,default=str,indent=2)+'\n');results.append({'strategy':label,'experiment_id':result[0],'scorecard_id':result[1]})
 print(json.dumps({'results':results}))
if __name__=='__main__':main()
