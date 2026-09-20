"""Derive immutable adjusted closes from frozen historical market evidence."""
import argparse, csv, json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import duckdb

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--group-name',required=True);p.add_argument('--market-source',required=True);p.add_argument('--start-date',required=True);p.add_argument('--end-date',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output)
    if out.exists(): raise FileExistsError('derived adjusted-close artifact already exists')
    start,end=date.fromisoformat(a.start_date),date.fromisoformat(a.end_date)
    con=duckdb.connect(a.db,read_only=True)
    universe_rows=con.execute("select u.as_of_trade_date,m.ticker from universe_snapshots u join universe_members m using(universe_snapshot_id) where u.group_name=?",[a.group_name]).fetchall()
    tickers={x[1] for x in universe_rows}
    snaps=con.execute("select trade_date,manifest_path from market_data_snapshots where source_channel=? and trade_date between ? and ? order by trade_date",[a.market_source,start,end]).fetchall()
    con.close()
    if not tickers or not snaps: raise ValueError('missing frozen universe or market snapshots')
    calendar=[x[0] for x in snaps]; index={d:i for i,d in enumerate(calendar)}
    required_start={(day,ticker) for day,ticker in universe_rows if day in index}
    required=set(required_start)
    required |= {(calendar[index[day]+5],ticker) for day,ticker in universe_rows if day in index and index[day]+5<len(calendar)}
    rows=[]; missing=[]
    for day,manifest_path in snaps:
        manifest=json.loads(Path(manifest_path).read_text())
        raw_path=Path(next(iter(manifest['artifacts'])))
        raw=json.loads(raw_path.read_text())
        daily={r['ts_code']:r for r in raw['responses']['daily']}
        factors={r['ts_code']:r for r in raw['responses']['adj_factor']}
        for ticker in sorted({t for d,t in required if d==day}):
            d=daily.get(ticker); f=factors.get(ticker)
            if d is None or f is None:
                if (day,ticker) in required_start: missing.append((str(day),ticker))
                continue
            rows.append({'trade_date':str(day),'ticker':ticker,'adj_close':str(Decimal(str(d['close']))*Decimal(str(f['adj_factor'])))})
    if missing: raise ValueError(f'missing frozen daily/adj_factor rows: {len(missing)}')
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=('trade_date','ticker','adj_close'));w.writeheader();w.writerows(rows)
    manifest={'artifact_type':'DERIVED_ADJUSTED_CLOSES_FROM_FROZEN_OOS_EVIDENCE','source_channel':a.market_source,'group_name':a.group_name,'date_range':[a.start_date,a.end_date],'ticker_count':len(tickers),'rows':len(rows),'sha256':sha256(out.read_bytes()).hexdigest()}
    out.with_suffix(out.suffix+'.manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    print(json.dumps(manifest,sort_keys=True))
if __name__=='__main__':main()
