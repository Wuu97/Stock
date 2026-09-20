"""Robustness audit for frozen Ridge OOS predictions; never trains or trades."""
import argparse, json, math
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import duckdb

HAC_LAG = 4  # fixed ex ante: T+5 forward outcomes overlap for four adjacent days
Z_95 = 1.959963984540054
def digest(p): return sha256(Path(p).read_bytes()).hexdigest()
def mean(xs): return sum(xs) / len(xs) if xs else None
def percentile(xs, q):
 xs=sorted(xs); x=(len(xs)-1)*q; lo=int(x); hi=math.ceil(x)
 return xs[lo] if lo==hi else xs[lo]*(hi-x)+xs[hi]*(x-lo)
def corr(x,y):
 mx,my=mean(x),mean(y); den=(sum((a-mx)**2 for a in x)*sum((b-my)**2 for b in y))**.5
 return sum((a-mx)*(b-my) for a,b in zip(x,y))/den if den else None
def ranks(xs):
 order=sorted(range(len(xs)),key=lambda i:xs[i]); out=[0.]*len(xs); i=0
 while i<len(order):
  j=i
  while j+1<len(order) and xs[order[j+1]]==xs[order[i]]: j+=1
  for k in order[i:j+1]: out[k]=(i+j+2)/2
  i=j+1
 return out
def hac(xs):
 n=len(xs); mu=mean(xs); lag=min(HAC_LAG,n-1)
 gamma=[]
 for k in range(lag+1): gamma.append(sum((xs[t]-mu)*(xs[t-k]-mu) for t in range(k,n))/n)
 long_run=gamma[0]+2*sum((1-k/(lag+1))*gamma[k] for k in range(1,lag+1))
 se=(max(long_run,0)/n)**.5
 return {'mean':mu,'hac_lag':lag,'hac_se':se,'ci_95':[mu-Z_95*se,mu+Z_95*se],'n_days':n}
def daily_distribution(xs):
 return {'n_days':len(xs),'positive_day_fraction':sum(x>0 for x in xs)/len(xs),'min':min(xs),'p10':percentile(xs,.1),'median':percentile(xs,.5),'p90':percentile(xs,.9),'max':max(xs),'hac_mean':hac(xs),'leave_one_best_out_mean':mean(sorted(xs)[:-1]),'leave_one_worst_out_mean':mean(sorted(xs)[1:])}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--predictions',required=True); ap.add_argument('--manifest',required=True); ap.add_argument('--dataset',required=True); ap.add_argument('--baseline-evaluation',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
 out=Path(a.output)
 if out.exists(): raise FileExistsError('robustness report already exists')
 manifest=json.loads(Path(a.manifest).read_text())
 if digest(a.predictions)!=manifest['artifact_sha256']: raise ValueError('prediction artifact hash mismatch')
 pred=[json.loads(x) for x in Path(a.predictions).read_text().splitlines()]
 con=duckdb.connect(':memory:')
 labels={(str(d),t):(v,s,str(av) if av else None) for d,t,v,s,av in con.execute("select trade_date,ticker,target_excess_ret_5d,label_status,label_available_trade_date from read_parquet(?)",[a.dataset]).fetchall()}; con.close()
 groups=defaultdict(list); excluded=defaultdict(Counter); contributions=defaultdict(lambda:defaultdict(float))
 for p in pred:
  value,status,available=labels.get((p['prediction_trade_date'],p['ticker']),(None,'LABEL_NOT_IN_DATASET',None))
  if status!='MATURE' or value is None: excluded[p['model_id']][status]+=1; continue
  if available is None or available<=p['prediction_trade_date']: raise ValueError('invalid non-forward label availability date')
  groups[(p['model_id'],p['prediction_trade_date'])].append((p['ticker'],float(p['prediction_score']),float(value)))
 daily=[]; pooled=defaultdict(lambda:defaultdict(list))
 for (model,day),rows in sorted(groups.items()):
  rows=sorted(rows,key=lambda x:x[1]); n=len(rows); buckets=[rows[i*n//5:(i+1)*n//5] for i in range(5)]
  q=[mean([r[2] for r in b]) for b in buckets]
  for i,b in enumerate(buckets):
   for ticker,_,ret in b:
    pooled[model][i+1].append(ret)
    contributions[model][ticker]+=ret/len(b) * (1 if i==4 else -1 if i==0 else 0)
  scores=[r[1] for r in rows]; actual=[r[2] for r in rows]
  daily.append({'model_id':model,'prediction_trade_date':day,'valid_predictions':n,'ic':corr(scores,actual),'rank_ic':corr(ranks(scores),ranks(actual)),'q':q,'spread':q[4]-q[0]})
 expected=json.loads(Path(a.baseline_evaluation).read_text())
 baseline={(r['model_id'],r['prediction_trade_date']):(r['ic'],r['rank_ic'],r['top_minus_bottom'],r['valid_predictions']) for r in expected['daily']}
 recalculated={(r['model_id'],r['prediction_trade_date']):(r['ic'],r['rank_ic'],r['spread'],r['valid_predictions']) for r in daily}
 def same_metric(a,b): return (a==b) if isinstance(a,int) else abs(a-b)<=1e-12
 if baseline.keys()!=recalculated.keys() or any(not all(same_metric(x,y) for x,y in zip(baseline[k],recalculated[k])) for k in baseline): raise ValueError('recalculated daily metrics differ from frozen evaluation')
 sections={}
 for model in sorted({p['model_id'] for p in pred}):
  ds=[r for r in daily if r['model_id']==model]; qs={str(i):mean(pooled[model][i]) for i in range(1,6)}
  stock=sorted(contributions[model].items(),key=lambda x:abs(x[1]),reverse=True)
  total=sum(abs(x[1]) for x in stock)
  sections[model]={'prediction_days':len({p['prediction_trade_date'] for p in pred if p['model_id']==model}),'valid_days':len(ds),'valid_predictions':sum(r['valid_predictions'] for r in ds),'excluded':dict(excluded[model]),'pooled_quintile_t5_excess':qs,'pooled_q5_minus_q1':qs['5']-qs['1'],'daily_ic':daily_distribution([r['ic'] for r in ds]),'daily_rank_ic':daily_distribution([r['rank_ic'] for r in ds]),'daily_q5_minus_q1':daily_distribution([r['spread'] for r in ds]),'top_absolute_stock_spread_contributors':[{'ticker':t,'contribution':v,'absolute_share_of_stock_contribution':abs(v)/total if total else None} for t,v in stock[:10]]}
 all_days=daily; all_q=defaultdict(list)
 for model in pooled:
  for i in range(1,6): all_q[i].extend(pooled[model][i])
 q={str(i):mean(all_q[i]) for i in range(1,6)}
 sections['all_mature_days']={'valid_days':len(all_days),'valid_predictions':sum(r['valid_predictions'] for r in all_days),'pooled_quintile_t5_excess':q,'pooled_q5_minus_q1':q['5']-q['1'],'daily_ic':daily_distribution([r['ic'] for r in all_days]),'daily_rank_ic':daily_distribution([r['rank_ic'] for r in all_days]),'daily_q5_minus_q1':daily_distribution([r['spread'] for r in all_days])}
 report={'artifact_type':'RIDGE_FROZEN_OOS_PREDICTION_ROBUSTNESS_AUDIT','method':{'primary':'daily-series Newey-West HAC two-sided 95% confidence intervals','fixed_hac_lag':HAC_LAG,'reason':'T+5 labels overlap across four adjacent prediction dates','no_trimming_in_primary_results':True},'lineage':{'prediction_artifact_sha256':digest(a.predictions),'manifest_sha256':digest(a.manifest),'dataset_sha256':digest(a.dataset),'baseline_evaluation_sha256':digest(a.baseline_evaluation)},'daily_recalculation_matches_baseline':True,'sections':sections,'limitations':['T+5 outcomes overlap, and HAC adjusts only this serial dependence; it does not create independent model experiments.','W1–W4 training windows substantially overlap; do not treat the four sections as four independent trials.','W4 has only 38 mature-label days.','This audit assesses scores versus labels, not execution, costs, holdings, or account PnL.']}
 out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); print(json.dumps({'sha256':digest(out),'valid_days':len(all_days)}))
if __name__=='__main__': main()
