"""Train and freeze the 13-model causal Ridge schedule, then emit scores."""
import argparse,json
from datetime import date,datetime,timezone
from hashlib import sha256
from pathlib import Path
import duckdb
import numpy as np
from quant_core.ml_shadow import FEATURE_COLUMNS,MODEL_SCHEMA_VERSION

def fh(p): return sha256(Path(p).read_bytes()).hexdigest()
def canon(x): return json.dumps(x,sort_keys=True,separators=(',',':'),default=str)
def main():
 p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--manifest',required=True);p.add_argument('--profile-id',required=True);p.add_argument('--profile-version',required=True);p.add_argument('--db',required=True);p.add_argument('--model-dir',required=True);p.add_argument('--schedule',required=True);p.add_argument('--predictions',required=True);p.add_argument('--prediction-manifest',required=True);a=p.parse_args()
 dataset_hash=fh(a.dataset); manifest_hash=fh(a.manifest); manifest=json.loads(Path(a.manifest).read_text())
 db=duckdb.connect(a.db,read_only=True); prow=db.execute('select profile_json,profile_sha256 from competition_profiles where competition_profile_id=? and competition_profile_version=?',[a.profile_id,a.profile_version]).fetchone()
 if not prow: raise ValueError('competition profile missing')
 profile=json.loads(prow[0]); profile_hash=prow[1]; group_id=profile['universe_binding']['group_id']; snap_rows=db.execute('select as_of_trade_date,universe_snapshot_id,count(*) from universe_snapshots u join universe_members m using(universe_snapshot_id) where u.group_name=? group by 1,2',[group_id]).fetchall(); db.close(); snapshot_by_date={d:(sid,n) for d,sid,n in snap_rows}
 if manifest.get('dataset_sha256')!=dataset_hash: raise ValueError('dataset manifest hash mismatch')
 c=duckdb.connect(':memory:'); rows=c.execute('select * from read_parquet(?) order by trade_date,ticker',[a.dataset]).fetchall(); cols=[x[0] for x in c.description]; c.close(); idx={x:i for i,x in enumerate(cols)}
 by={}
 for r in rows: by.setdefault(r[idx['trade_date']],[]).append(r)
 dates=sorted(by); exec_dates=[d for d in dates if d>=date(2023,9,12)]
 if len(exec_dates)!=727 or any(d not in snapshot_by_date or snapshot_by_date[d][1]!=50 for d in exec_dates): raise ValueError('PIT Universe coverage is incomplete for execution dates')
 windows=[]
 for off in range(0,len(exec_dates),60):
  test=exec_dates[off:off+60]; cutoff=dates[dates.index(test[0])-1]; eligible=[]
  for d in dates:
   if d>=test[0]: break
   if all(r[idx['label_status']]=='UNTRADEABLE_OUTCOME' or (r[idx['label_status']]=='MATURE' and r[idx['label_available_trade_date']] is not None and r[idx['label_available_trade_date']]<=cutoff) for r in by[d]): eligible.append(d)
  train=eligible[-480:]
  if len(train)!=480: raise ValueError('causal 480-day contract failed at '+str(test[0]))
  windows.append((train,test,cutoff))
 model_dir=Path(a.model_dir); schedule_path=Path(a.schedule); pred_path=Path(a.predictions); pred_manifest=Path(a.prediction_manifest)
 if schedule_path.exists() or pred_path.exists() or pred_manifest.exists(): raise FileExistsError('full Ridge artifact already exists')
 model_dir.mkdir(parents=True,exist_ok=True); c=duckdb.connect(':memory:'); model_entries=[]
 for number,(train,test,cutoff) in enumerate(windows,1):
  train_rows=[r for d in train for r in by[d] if r[idx['label_status']]=='MATURE' and r[idx['label_available_trade_date']]<=cutoff]
  matrix=np.asarray([[r[idx[f]] for f in FEATURE_COLUMNS] for r in train_rows],float); labels=np.asarray([r[idx['target_excess_ret_5d']] for r in train_rows],float); means=matrix.mean(0); scales=matrix.std(0); scales[scales==0]=1.; norm=(matrix-means)/scales; design=np.column_stack((np.ones(len(norm)),norm)); penalty=np.eye(design.shape[1]); penalty[0,0]=0.; weights=np.linalg.solve(design.T@design+penalty,design.T@labels)
  config_hash=sha256(canon({'ridge_alpha':1.0,'feature_columns':list(FEATURE_COLUMNS),'standardization':'training_mean_population_std'}).encode()).hexdigest()
  payload={'schema_version':MODEL_SCHEMA_VERSION,'model_type':'ridge_excess_return_v1','created_at':datetime.now(timezone.utc).isoformat(),'trained_through_date':str(cutoff),'label_availability_cutoff_date':str(cutoff),'training_decision_dates':[str(train[0]),str(train[-1])],'training_rows':len(train_rows),'ridge_alpha':1.0,'model_config_sha256':config_hash,'feature_columns':list(FEATURE_COLUMNS),'feature_means':dict(zip(FEATURE_COLUMNS,map(float,means))),'feature_scales':dict(zip(FEATURE_COLUMNS,map(float,scales))),'coefficients':dict(zip(FEATURE_COLUMNS,map(float,weights[1:]))),'intercept':float(weights[0]),'source_dataset_sha256':dataset_hash,'source_dataset_manifest_sha256':manifest_hash,'profile':{'id':a.profile_id,'version':a.profile_version}}
  path=model_dir/f'full_w{number:02d}.json';path.write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n'); model_entries.append({'model_id':path.stem,'model_path':str(path),'model_sha256':fh(path),'model_config_sha256':config_hash,'training_decision_dates':[str(train[0]),str(train[-1])],'label_availability_cutoff_date':str(cutoff),'prediction_dates':[str(test[0]),str(test[-1])],'training_rows':len(train_rows)})
 schedule={'schema_version':'ridge_model_schedule_v1','dataset_sha256':dataset_hash,'dataset_manifest_sha256':manifest_hash,'competition_profile':{'id':a.profile_id,'version':a.profile_version,'hash':profile_hash},'models':model_entries}; schedule_path.parent.mkdir(parents=True,exist_ok=True);schedule_path.write_text(json.dumps(schedule,indent=2,sort_keys=True)+'\n');schedule_hash=fh(schedule_path)
 # Generate only from frozen dataset features; labels are never read in this pass.
 predictions=[]
 for entry,(train,test,cutoff) in zip(model_entries,windows):
  model=json.loads(Path(entry['model_path']).read_text());
  for d in test:
   cross=by[d]
   if len(cross)!=50: raise ValueError('prediction cross-section is not 50 rows at '+str(d))
   for r in cross:
    values={f:float(r[idx[f]]) for f in FEATURE_COLUMNS}; score=model['intercept']+sum(model['coefficients'][f]*((values[f]-model['feature_means'][f])/model['feature_scales'][f]) for f in FEATURE_COLUMNS)
    predictions.append({'prediction_trade_date':str(d),'ticker':r[idx['ticker']],'model_id':entry['model_id'],'model_sha256':entry['model_sha256'],'prediction_score':float(score),'prediction_timing':'T_close','pit_universe_snapshot_id':snapshot_by_date[d][0],'feature_snapshot_hash':sha256(canon(values).encode()).hexdigest(),'feature_schema_hash':sha256(canon(list(FEATURE_COLUMNS)).encode()).hexdigest(),'schedule_hash':schedule_hash,'profile_hash':profile_hash,'dataset_sha256':dataset_hash,'dataset_manifest_sha256':manifest_hash})
 if len(predictions)!=36350: raise ValueError('prediction count is not 727*50')
 pred_path.parent.mkdir(parents=True,exist_ok=True);pred_path.write_text('\n'.join(canon(x) for x in predictions)+'\n'); artifact_hash=fh(pred_path); pred_manifest.write_text(json.dumps({'artifact_type':'RIDGE_V4_FULL_OOS_PREDICTIONS','status':'MODELS_AND_PREDICTIONS_READY','rows':len(predictions),'prediction_dates':['2023-09-12','2026-09-11'],'dataset_sha256':dataset_hash,'dataset_manifest_sha256':manifest_hash,'schedule_sha256':schedule_hash,'artifact_sha256':artifact_hash,'models':model_entries},indent=2,sort_keys=True)+'\n'); print(json.dumps({'models':len(model_entries),'predictions':len(predictions),'schedule_sha256':schedule_hash,'artifact_sha256':artifact_hash}))
if __name__=='__main__':main()
