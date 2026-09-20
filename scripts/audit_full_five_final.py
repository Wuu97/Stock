import json, hashlib
from pathlib import Path
import numpy as np, pandas as pd
import duckdb

ROOT=Path('data/ridge_history_evidence'); OUT=ROOT/'full_five_results'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def hac_mean(x,lag=4):
 x=np.asarray(x,float); x=x[np.isfinite(x)]; n=len(x)
 if n<2:return {'mean':float(np.mean(x)) if n else None,'se':None,'ci95':[None,None],'n':n}
 z=x-x.mean(); gamma=[np.mean(z[:n-k]*z[k:]) for k in range(lag+1)]
 var=gamma[0]+2*sum((1-k/(lag+1))*gamma[k] for k in range(1,min(lag,n-1)+1)); se=(max(var,0)/n)**.5
 return {'mean':float(x.mean()),'se':float(se),'ci95':[float(x.mean()-1.96*se),float(x.mean()+1.96*se)],'n':n}
def main():
 pred=pd.read_json(ROOT/'full_ridge_predictions_v2.jsonl',lines=True); pred['trade_date']=pd.to_datetime(pred.prediction_trade_date)
 ds=pd.read_parquet(ROOT/'ridge_v4_unified_dataset_v2.parquet'); ds['trade_date']=pd.to_datetime(ds.trade_date)
 j=pred.merge(ds[['trade_date','ticker','target_excess_ret_5d','label_status']],on=['trade_date','ticker'],how='left',validate='one_to_one')
 valid=j[(j.label_status=='MATURE') & j.target_excess_ret_5d.notna()].copy(); valid['q']=valid.groupby('trade_date').prediction_score.transform(lambda s: pd.qcut(s.rank(method='first'),5,labels=False)+1 if len(s)>=5 else np.nan)
 daily=[]
 for d,g in valid.groupby('trade_date'):
  if len(g)<5: continue
  ic=g.prediction_score.corr(g.target_excess_ret_5d,method='pearson'); ric=g.prediction_score.rank().corr(g.target_excess_ret_5d.rank())
  q=g.groupby('q').target_excess_ret_5d.mean(); daily.append({'date':str(d.date()),'ic':ic,'rank_ic':ric,'q1':q.get(1,np.nan),'q2':q.get(2,np.nan),'q3':q.get(3,np.nan),'q4':q.get(4,np.nan),'q5':q.get(5,np.nan),'spread':q.get(5,np.nan)-q.get(1,np.nan),'n':len(g)})
 daily=pd.DataFrame(daily)
 def quality(g):
  return {'days':int(len(g)),'samples':int(valid[valid.trade_date.isin(pd.to_datetime(g.date))].shape[0]),'ic':hac_mean(g.ic),'rank_ic':hac_mean(g.rank_ic),'q1':hac_mean(g.q1),'q2':hac_mean(g.q2),'q3':hac_mean(g.q3),'q4':hac_mean(g.q4),'q5':hac_mean(g.q5),'q5_q1':hac_mean(g.spread),'avg_daily_n':float(g.n.mean()) if len(g) else 0}
 qwin={}
 for mid,g in pred.groupby('model_id'):
  qwin[mid]=quality(daily[daily.date.isin(g.trade_date.dt.strftime('%Y-%m-%d'))])
 quality_all=quality(daily)
 con=duckdb.connect('data/top50/quant.duckdb',read_only=True)
 accounts={}; periods={'full':('2023-09-12','2026-09-11'),'pre_223':('2023-09-12','2025-10-16'),'short_223':('2025-10-17','2026-09-11'),'2023_partial':('2023-09-12','2023-12-31'),'2024':('2024-01-01','2024-12-31'),'2025':('2025-01-01','2025-12-31'),'2026_partial':('2026-01-01','2026-09-11')}
 for label in ('baseline','kdj','macd','pure','ridge'):
  a='competition_v4_ridge_3y_'+label; nav=pd.DataFrame(con.execute('select trade_date,total_equity,cash_balance,securities_value,max_drawdown from sim_nav_daily where account_id=? order by trade_date',[a]).fetchall(),columns=['date','equity','cash','sec','dd']); nav['date']=pd.to_datetime(nav.date); ex=pd.DataFrame(con.execute('select trade_date,commission,stamp_duty,transfer_fee from sim_executions where account_id=?',[a]).fetchall(),columns=['date','commission','stamp','transfer']); ex['date']=pd.to_datetime(ex.date)
  seg={}
  for pn,(s,e) in periods.items():
   x=nav[(nav.date>=pd.Timestamp(s))&(nav.date<=pd.Timestamp(e))]; exx=ex[(ex.date>=pd.Timestamp(s))&(ex.date<=pd.Timestamp(e))]; seg[pn]={'start':s,'end':e,'days':len(x),'return':float(x.equity.iloc[-1]/x.equity.iloc[0]-1) if len(x) else None,'max_drawdown':float(x.dd.min()) if len(x) else None,'turnover':None,'fees':float(exx[['commission','stamp','transfer']].sum().sum()) if len(exx) else 0.0,'fills':int(len(exx))}
  accounts[label]={'segments':seg,'end_equity':float(nav.equity.iloc[-1]),'end_cash':float(nav.cash.iloc[-1]),'end_securities':float(nav.sec.iloc[-1]),'nav_days':len(nav)}
  if label=='ridge':
   rw={}
   for mid,g in pred.groupby('model_id'):
    s,e=g.trade_date.min(),g.trade_date.max(); x=nav[(nav.date>=s)&(nav.date<=e)]; rw[mid]={'start':str(s.date()),'end':str(e.date()),'return':float(x.equity.iloc[-1]/x.equity.iloc[0]-1) if len(x)>1 else None,'days':len(x)}
   accounts[label]['model_windows']=rw
 audit={'status':'FINAL_RESEARCH_AUDIT','frozen_hashes':{k:sha(ROOT/f) for k,f in {'dataset':'ridge_v4_unified_dataset_v2.parquet','manifest':'ridge_v4_unified_dataset_v2.parquet.manifest.json','schedule':'full_ridge_schedule_v2.json','predictions':'full_ridge_predictions_v2.jsonl'}.items()},'prediction_coverage':{'rows':len(pred),'days':pred.trade_date.nunique(),'mature_rows':len(valid),'mature_days':valid.trade_date.nunique(),'unmatured_rows':int((j.label_status=='UNMATURED').sum()),'untradeable_rows':int((j.label_status=='UNTRADEABLE_OUTCOME').sum())},'method':{'hac':'Newey-West HAC','lag':4,'ci':'two-sided 95%','daily_observation_unit':'trade_date','note':'overlapping T+5 labels; windows not independent'},'prediction_quality':{'overall':quality_all,'by_model':qwin},'accounts':accounts,'limitations':['Account ledger does not provide a canonical realized/unrealized P&L decomposition table; segment results use NAV and available execution fees.','Prediction quality is descriptive and does not establish causal trading profitability.','Overlapping labels and historical overlap with prior 223-day study preclude independent-experiment interpretation.']}
 p=OUT/'competition_v4_ridge_3y_final_research_audit.json'; p.write_text(json.dumps(audit,indent=2,default=str)+'\n'); print(json.dumps({'path':str(p),'sha256':sha(p),'quality':quality_all},indent=2))
if __name__=='__main__': main()
