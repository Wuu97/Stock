"""Build feature rows for execution dates whose T+5 labels are not available."""
import argparse, csv, json
from datetime import date
from decimal import Decimal
from pathlib import Path
import duckdb

FEATURES=('close','momentum_5d','momentum_20d','sma20_deviation','volume_ratio_20d','momentum_20d_percentile','momentum_20d_zscore')
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--group-name',required=True);p.add_argument('--market-prefix',required=True);p.add_argument('--start-date',required=True);p.add_argument('--end-date',required=True);p.add_argument('--output',required=True);a=p.parse_args(); out=Path(a.output)
 if out.exists(): raise FileExistsError('tail artifact already exists')
 start,end=date.fromisoformat(a.start_date),date.fromisoformat(a.end_date)
 c=duckdb.connect(a.db,read_only=True)
 members=c.execute("select u.as_of_trade_date,m.ticker from universe_snapshots u join universe_members m using(universe_snapshot_id) where u.group_name=? and u.as_of_trade_date between ? and ? order by 1,2",[a.group_name,start,end]).fetchall()
 ids=[r[0] for r in c.execute("select market_snapshot_id from market_data_snapshots where market_snapshot_id like ? order by trade_date",[a.market_prefix+'%']).fetchall()]
 bars=c.execute("select trade_date,ticker,close,volume,status from daily_bars where market_snapshot_id in ("+','.join('?'*len(ids))+") and ticker in (select distinct m.ticker from universe_snapshots u join universe_members m using(universe_snapshot_id) where u.group_name=? ) order by trade_date,ticker",ids+[a.group_name]).fetchall();c.close()
 by={}
 for d,t,cl,vol,status in bars:
  if status=='TRADING': by.setdefault(t,[]).append((d,Decimal(str(cl)),Decimal(str(vol))))
 rows=[]
 for day,ticker in members:
  hist=[x for x in by.get(ticker,[]) if x[0]<=day]
  if len(hist)<20 or hist[-1][0]!=day: raise ValueError('missing tail feature history for '+str(day)+' '+ticker)
  w=hist[-20:]; closes=[x[1] for x in w]; vols=[x[2] for x in w]; close=closes[-1]; mom20=float(close/closes[0]-1); values=[float((hist2[-1][1]/hist2[-21][1])-1) for hist2 in [ [x for x in by.get(t,[]) if x[0]<=day] for _,t in members if _==day ] if len(hist2)>=21]
  # Cross-sectional statistics are computed over the same day's 50 PIT members.
  rows.append((day,ticker,float(close),float(close/closes[-6]-1),mom20,float(close/(sum(closes)/20)-1),float(vols[-1]/(sum(vols)/20)) if sum(vols) else 0.0,None,None,None,'UNMATURED',None))
 # fill cross-sectional percentile/zscore without using future observations
 for day in sorted({r[0] for r in rows}):
  idx=[i for i,r in enumerate(rows) if r[0]==day]; vals=[rows[i][4] for i in idx]; ordered=sorted(vals); mu=sum(vals)/len(vals); sd=(sum((v-mu)**2 for v in vals)/len(vals))**0.5
  for i,v in zip(idx,vals): rows[i]=rows[i][:7]+((sum(x>=v for x in ordered)-.5)/len(vals),(v-mu)/sd if sd else 0.0)+rows[i][9:]
 out.parent.mkdir(parents=True,exist_ok=True)
 with out.open('w',newline='') as f:
  w=csv.writer(f);w.writerow(('trade_date','ticker','close','momentum_5d','momentum_20d','sma20_deviation','volume_ratio_20d','momentum_20d_percentile','momentum_20d_zscore','target_excess_ret_5d','label_status','label_available_trade_date'));w.writerows(rows)
 out.with_suffix(out.suffix+'.manifest.json').write_text(json.dumps({'artifact_type':'UNMATURED_EXECUTION_TAIL_FEATURES','date_range':[a.start_date,a.end_date],'rows':len(rows)},indent=2)+'\n')
 print(json.dumps({'rows':len(rows),'output':str(out)}))
if __name__=='__main__': main()
