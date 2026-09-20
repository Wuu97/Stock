"""Freeze and validate the three-part Ridge dataset."""
import argparse, json
from datetime import date
from hashlib import sha256
from pathlib import Path
import duckdb

def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--training',required=True); p.add_argument('--bridge',required=True); p.add_argument('--execution',required=True); p.add_argument('--tail',required=True); p.add_argument('--output',required=True); a=p.parse_args(); out=Path(a.output)
    parts=[a.training,a.bridge,a.execution,a.tail]
    if out.exists(): raise FileExistsError('unified dataset already exists')
    c=duckdb.connect(':memory:')
    c.execute("create table dataset as select * from read_parquet(?)",[a.training])
    c.execute("insert into dataset select * from read_parquet(?)",[a.bridge]); c.execute("insert into dataset select * from read_parquet(?)",[a.execution]); c.execute("insert into dataset select * from read_csv(?,auto_detect=true,header=true)",[a.tail])
    duplicate=c.execute("select count(*)-count(distinct cast(trade_date as varchar)||'|'||ticker) from dataset").fetchone()[0]
    if duplicate: raise ValueError('duplicate decision_trade_date/ticker rows')
    if c.execute("select count(*) from dataset where label_status='UNMATURED' and target_excess_ret_5d is not null").fetchone()[0]: raise ValueError('unmatured target is not null')
    counts=c.execute('select trade_date,count(*) from dataset group by 1 order by 1').fetchall()
    if len(counts)!=1212 or any(n!=50 for _,n in counts): raise ValueError('expected 1212 dates with 50 rows each')
    out.parent.mkdir(parents=True,exist_ok=True); c.execute('copy dataset to ? (format parquet)',[str(out)]); c.close()
    c=duckdb.connect(); rows=c.execute('select * from read_parquet(?) order by trade_date,ticker',[str(out)]).fetchall(); c.close()
    by={}
    for r in rows: by.setdefault(r[0],[]).append(r)
    dates=sorted(by); exec_dates=[d for d in dates if d>=date(2023,9,12)]; windows=[]
    for offset in range(0,len(exec_dates),60):
        test=exec_dates[offset:offset+60]; cutoff=dates[dates.index(test[0])-1]; eligible=[]
        for d in dates:
            if d>=test[0]: break
            # A decision date remains part of the 480-day window when some
            # securities have UNTRADEABLE_OUTCOME; those rows are excluded
            # from training, but the decision date is not silently removed.
            if all((r[10]=='UNTRADEABLE_OUTCOME') or (r[10]=='MATURE' and r[11] is not None and r[11]<=cutoff) for r in by[d]): eligible.append(d)
        train=eligible[-480:]
        if len(train)!=480: raise ValueError('causal window lacks 480 days at '+str(test[0]))
        windows.append({'test_start':str(test[0]),'test_end':str(test[-1]),'cutoff':str(cutoff),'train_start':str(train[0]),'train_end':str(train[-1]),'training_rows':sum(len(by[d]) for d in train)})
    report={'artifact_type':'RIDGE_V4_UNIFIED_DATASET','status':'DATASET_READY_FOR_SCHEDULE','rows':len(rows),'decision_days':len(dates),'mature_rows':sum(r[10]=='MATURE' for r in rows),'untradeable_rows':sum(r[10]=='UNTRADEABLE_OUTCOME' for r in rows),'unmatured_rows':sum(r[10]=='UNMATURED' for r in rows),'components':{x:digest(x) for x in parts},'dataset_sha256':digest(out),'windows':windows}
    Path(str(out)+'.manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__': main()
