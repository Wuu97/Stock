"""Evaluate frozen OOS scores only against already-mature frozen T+5 labels."""
import argparse, json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
import duckdb

def mean(xs): return sum(xs)/len(xs) if xs else None
def corr(x,y):
 if len(x)<2:return None
 mx,my=mean(x),mean(y); den=(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))**.5
 return sum((a-mx)*(b-my) for a,b in zip(x,y))/den if den else None
def ranks(xs):
 order=sorted(range(len(xs)),key=lambda i:xs[i]); result=[0.]*len(xs); i=0
 while i<len(order):
  j=i
  while j+1<len(order) and xs[order[j+1]]==xs[order[i]]:j+=1
  value=(i+j+2)/2
  for k in order[i:j+1]:result[k]=value
  i=j+1
 return result
def digest(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--predictions',required=True);p.add_argument('--prediction-manifest',required=True);p.add_argument('--dataset',required=True);p.add_argument('--output',required=True);a=p.parse_args()
 out=Path(a.output)
 if out.exists():raise FileExistsError('evaluation artifact already exists')
 manifest=json.loads(Path(a.prediction_manifest).read_text())
 if manifest['artifact_sha256']!=digest(a.predictions):raise ValueError('prediction artifact hash mismatch')
 predictions=[json.loads(line) for line in Path(a.predictions).read_text().splitlines()]
 c=duckdb.connect(':memory:'); cur=c.execute("select trade_date,ticker,target_excess_ret_5d,label_status,label_available_trade_date from read_parquet(?)",[a.dataset]); labels={(str(d),t):(v,s,str(av) if av else None) for d,t,v,s,av in cur.fetchall()};c.close()
 by_day=defaultdict(list); excluded=defaultdict(lambda:defaultdict(int))
 for r in predictions:
  value,status,available=labels.get((r['prediction_trade_date'],r['ticker']),(None,'LABEL_NOT_IN_DATASET',None))
  if status!='MATURE' or value is None:excluded[r['model_id']][status]+=1;continue
  # A T+5 outcome necessarily becomes available *after* its prediction date.
  # This is an ex-post evaluation only; neither the reconstruction nor the
  # frozen model may consume this value.  A non-forward availability date
  # violates the label contract rather than making the observation usable.
  if available is None or available<=r['prediction_trade_date']:raise ValueError('invalid non-forward label availability date')
  by_day[(r['model_id'],r['prediction_trade_date'])].append((float(r['prediction_score']),float(value)))
 daily=[]; grouped=defaultdict(lambda:defaultdict(list))
 for (model,day),rows in sorted(by_day.items()):
  scores,actuals=zip(*rows); ic=corr(scores,actuals); ric=corr(ranks(scores),ranks(actuals)); n=len(rows)
  ranked=sorted(rows,key=lambda x:x[0]); buckets=[ranked[(i*n)//5:((i+1)*n)//5] for i in range(5)]
  means=[mean([x[1] for x in b]) for b in buckets]
  daily.append({'model_id':model,'prediction_trade_date':day,'valid_predictions':n,'ic':ic,'rank_ic':ric,'quintile_future_excess_returns':means,'top_minus_bottom':means[-1]-means[0] if None not in (means[-1],means[0]) else None})
  for i,b in enumerate(buckets):grouped[model][i+1].extend(x[1] for x in b)
 summary=[]
 for model in sorted({r['model_id'] for r in predictions}):
  rows=[r for r in daily if r['model_id']==model]; q={str(i):mean(grouped[model][i]) for i in range(1,6)}
  model_predictions=[r for r in predictions if r['model_id']==model]
  summary.append({'model_id':model,'prediction_days':len({r['prediction_trade_date'] for r in model_predictions}),'prediction_rows':len(model_predictions),'valid_prediction_days':len(rows),'valid_predictions':sum(r['valid_predictions'] for r in rows),'mean_daily_ic':mean([r['ic'] for r in rows if r['ic'] is not None]),'mean_daily_rank_ic':mean([r['rank_ic'] for r in rows if r['rank_ic'] is not None]),'quintile_future_excess_returns':q,'top_minus_bottom':q['5']-q['1'] if q['5'] is not None and q['1'] is not None else None,'excluded':dict(excluded[model])})
 payload={'artifact_type':'FROZEN_OOS_PREDICTION_QUALITY_EVALUATION','prediction_artifact_sha256':manifest['artifact_sha256'],'prediction_manifest_sha256':digest(a.prediction_manifest),'dataset_sha256':digest(a.dataset),'label_contract':'T_PLUS_5_MATURE_ONLY; evaluation joins labels only after their availability date, while prediction generation remains label-free','summary':summary,'daily':daily,'note':'Daily cross-sections are correlated over time; summary is descriptive, not an independent-observation significance test.'}
 out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n');print(json.dumps({'evaluation_sha256':digest(out),'models':len(summary),'daily_rows':len(daily)}))
if __name__=='__main__':main()
