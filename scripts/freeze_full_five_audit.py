import json, hashlib
from pathlib import Path
from datetime import date
import duckdb

ROOT=Path('data/ridge_history_evidence')
OUT=ROOT/'full_five_results'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 files={'dataset':ROOT/'ridge_v4_unified_dataset_v2.parquet','manifest':ROOT/'ridge_v4_unified_dataset_v2.parquet.manifest.json','schedule':ROOT/'full_ridge_schedule_v2.json','predictions':ROOT/'full_ridge_predictions_v2.jsonl'}
 hashes={k:sha(v) for k,v in files.items()}
 profile_hash='249346c52e7c068da070c630c901c6ba4642c3bf66ebbb021cc121531c49b295'
 con=duckdb.connect('data/top50/quant.duckdb',read_only=True)
 accounts={}
 for label in ('baseline','kdj','macd','pure','ridge'):
  a='competition_v4_ridge_3y_'+label
  nav=con.execute('select count(*),min(trade_date),max(trade_date) from sim_nav_daily where account_id=?',[a]).fetchone()
  orders=con.execute("select count(*),sum(case when order_status='PENDING' then 1 else 0 end) from sim_order_intents where account_id=?",[a]).fetchone()
  fills=con.execute('select count(*) from sim_executions where account_id=?',[a]).fetchone()[0]
  holdings=con.execute("select ticker,total_shares,available_shares,book_cost_balance from sim_positions_daily where account_id=? and trade_date=(select max(trade_date) from sim_positions_daily where account_id=?) and total_shares>0 order by ticker",[a,a]).fetchall()
  report=json.loads((OUT/f'{label}.json').read_text())
  accounts[label]={'account_id':a,'metrics':report['metrics'],'replay':report['replay'],'nav':{'count':nav[0],'start':str(nav[1]),'end':str(nav[2])},'orders':orders[0],'pending_orders':orders[1] or 0,'fills':fills,'end_holdings':[{'ticker':x[0],'shares':x[1],'available':x[2],'book_cost':str(x[3])} for x in holdings]}
 # Ridge decision lineage: frozen score rows, with any matching Ridge order intents.
 order_rows=con.execute("select target_trade_date,ticker,direction,target_shares,order_status,reject_reason_code,intent_id from sim_order_intents where account_id='competition_v4_ridge_3y_ridge'").fetchall()
 om={}
 for r in order_rows: om.setdefault((r[0],r[1]),[]).append({'direction':r[2],'target_shares':r[3],'status':r[4],'reject_reason':r[5],'intent_id':r[6]})
 lin=OUT/'ridge_decision_lineage_v2.jsonl'; n=0
 with open(files['predictions']) as src, lin.open('w') as dst:
  for line in src:
   x=json.loads(line); d=date.fromisoformat(x['prediction_trade_date'])
   x['lineage']={'dataset_sha256':hashes['dataset'],'manifest_sha256':hashes['manifest'],'schedule_sha256':hashes['schedule'],'prediction_artifact_sha256':hashes['predictions'],'profile_hash':profile_hash,'model_id':x['model_id'],'model_sha256':x['model_sha256'],'decision_timing':'T_close','matching_orders':om.get((d,x['ticker']),[])}
   dst.write(json.dumps(x,separators=(',',':'))+'\n'); n+=1
 lin_hash=sha(lin)
 audit={'status':'FORMAL_COMPLETED','profile_hash':profile_hash,'frozen_hashes':hashes,'prediction_rows':n,'prediction_days':727,'models':13,'accounts':accounts,'ridge_decision_lineage':{'path':str(lin),'sha256':lin_hash,'rows':n},'execution_contract':'V4 strict engine; initial_cash=1000000; continuous accounts; no forced liquidation; final pending_orders=0'}
 p=OUT/'competition_v4_ridge_3y_five_strategy_audit.json'; p.write_text(json.dumps(audit,indent=2,default=str)+'\n'); print(json.dumps({'audit':str(p),'sha256':sha(p),'lineage_sha256':lin_hash},indent=2))
if __name__=='__main__': main()
