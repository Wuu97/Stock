"""Reconstruct frozen Ridge OOS scores without labels or trading replay."""
import argparse, json
from datetime import date
from hashlib import sha256
from pathlib import Path
import duckdb
from quant_core.database import read_connection
from quant_core.market_data import MarketDataStore
from quant_core.ml_shadow import build_ml_shadow_features
from quant_core.ridge_schedule import RidgeModelSchedule

def canon(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
def digest(x): return sha256(canon(x).encode()).hexdigest()
def file_hash(p): return sha256(Path(p).read_bytes()).hexdigest()

def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--dataset',required=True); p.add_argument('--dataset-manifest',required=True); p.add_argument('--profile-id',required=True); p.add_argument('--profile-version',required=True); p.add_argument('--schedule',required=True); p.add_argument('--output',required=True); p.add_argument('--manifest-output',required=True); args=p.parse_args()
 out,manifest=Path(args.output),Path(args.manifest_output)
 if out.exists() or manifest.exists(): raise FileExistsError('prediction artifact is immutable and already exists')
 schedule=RidgeModelSchedule.load(Path(args.schedule)); schedule_hash=schedule.artifact_sha256
 dataset_hash=file_hash(args.dataset); manifest_hash=file_hash(args.dataset_manifest)
 dataset_manifest=json.loads(Path(args.dataset_manifest).read_text())
 if dataset_manifest.get('dataset_sha256')!=dataset_hash: raise ValueError('dataset manifest hash mismatch')
 with read_connection(args.db) as c:
  profile_row=c.execute('select profile_json,profile_sha256 from competition_profiles where competition_profile_id=? and competition_profile_version=?',[args.profile_id,args.profile_version]).fetchone()
  if not profile_row: raise ValueError('profile missing')
  profile=json.loads(profile_row[0]); profile_hash=profile_row[1]
  expected={'id':args.profile_id,'version':args.profile_version,'hash':profile_hash}
  raw_schedule=json.loads(Path(args.schedule).read_text())
  if raw_schedule.get('competition_profile')!=expected or raw_schedule.get('dataset_sha256')!=dataset_hash or raw_schedule.get('dataset_manifest_sha256')!=manifest_hash: raise ValueError('frozen lineage mismatch')
  start,end=map(date.fromisoformat,profile['market_data_binding']['date_range'])
  days=[r[0] for r in c.execute('select distinct trade_date from market_data_snapshots where source_channel=? and trade_date between ? and ? order by 1',[profile['market_data_binding']['source'],start,end]).fetchall()]
  if len(days)!=223: raise ValueError('prediction calendar is not 223 trading days')
  universe_rows=c.execute('select u.as_of_trade_date,u.universe_snapshot_id,m.ticker from universe_snapshots u join universe_members m on m.universe_snapshot_id=u.universe_snapshot_id where u.group_name=? and u.as_of_trade_date between ? and ? order by 1,3',[profile['universe_binding']['group_id'],start,end]).fetchall()
  universe={}; snapshot={}
  for d,s,t in universe_rows: universe.setdefault(d,set()).add(t); snapshot[d]=s
  if set(universe)!=set(days): raise ValueError('PIT Universe calendar mismatch')
  tickers=set().union(*universe.values()); warmup=date.fromisoformat(profile['feature_warmup_market_binding']['date_range'][0]); snaps=[r[0] for r in c.execute('select market_snapshot_id from market_data_snapshots where source_channel=? and trade_date between ? and ? order by 1',[profile['market_data_binding']['source'],warmup,end]).fetchall()]
  bars=MarketDataStore(c).load_bars_many_for_tickers(snaps,tickers)
  by_model={}
  for e in schedule.entries: by_model.update({d:e for d in days if e.prediction_start<=d<=e.prediction_end})
  rows=[]; coverage=[]
  for d in days:
   entry=by_model[d]; model=schedule.model_for(d); features=build_ml_shadow_features(bars,d); fmap={f.ticker:f for f in features}; scores=[]; excluded=[]
   for ticker in sorted(universe[d]):
    f=fmap.get(ticker)
    if f is None: excluded.append({'ticker':ticker,'reason':'FEATURE_HISTORY_OR_TRADING_BAR_MISSING'}); continue
    feature_hash=digest({'schema':'ml_shadow_ridge_v1','values':dict(sorted(f.values.items()))})
    rows.append({'prediction_trade_date':str(d),'ticker':ticker,'model_id':entry.model_path.stem,'model_sha256':entry.model_sha256,'prediction_score':model.predict(f.values),'prediction_timing':'T_close','pit_universe_snapshot_id':snapshot[d],'feature_snapshot_hash':feature_hash,'feature_schema_hash':digest(['momentum_5d','momentum_20d','sma20_deviation','volume_ratio_20d','momentum_20d_percentile','momentum_20d_zscore']),'schedule_hash':schedule_hash,'profile_hash':profile_hash})
   coverage.append({'prediction_trade_date':str(d),'pit_universe_count':len(universe[d]),'successful_predictions':len(universe[d])-len(excluded),'excluded':excluded})
  if len({r['prediction_trade_date'] for r in rows})!=223: raise ValueError('prediction coverage is incomplete')
  out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(canon(r) for r in rows)+'\n')
  payload={'artifact_type':'RECONSTRUCTED_OOS_PREDICTIONS','reconstruction_time':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'prediction_dates':[str(start),str(end)],'rows':len(rows),'dataset_sha256':dataset_hash,'dataset_manifest_sha256':manifest_hash,'schedule_sha256':schedule_hash,'profile':expected,'models':[{'model_id':e.model_path.stem,'model_sha256':e.model_sha256,'prediction_dates':[str(e.prediction_start),str(e.prediction_end)]} for e in schedule.entries],'coverage':coverage,'artifact_sha256':file_hash(out)}
  manifest.parent.mkdir(parents=True,exist_ok=True); manifest.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
  print(json.dumps({'rows':len(rows),'artifact_sha256':payload['artifact_sha256'],'manifest_sha256':file_hash(manifest),'excluded':sum(len(x['excluded']) for x in coverage)}))
if __name__=='__main__': main()
