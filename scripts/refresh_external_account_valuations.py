"""Mark latest actual-account snapshots to immutable daily closes; never trades."""
import argparse
from quant_core.database import writer_connection
def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--trade-date',required=True); a=p.parse_args()
 with writer_connection(a.db) as c:
  rows=c.execute("""WITH latest AS (SELECT account_id,snapshot_id,ROW_NUMBER() OVER(PARTITION BY account_id ORDER BY observed_at DESC,created_at DESC) n FROM external_account_snapshots)
  SELECT x.snapshot_id,h.ticker,h.shares,h.unit_cost,b.close FROM latest x JOIN external_account_snapshot_holdings h USING(snapshot_id) JOIN daily_bars b ON b.ticker=h.ticker AND b.trade_date=? WHERE x.n=1""",[a.trade_date]).fetchall()
  c.executemany("INSERT OR REPLACE INTO external_account_daily_valuations VALUES (?,?,?,?,?,?,?)",[(s,a.trade_date,t,close,shares*close,shares*(close-cost),(close-cost)/cost) for s,t,shares,cost,close in rows])
 print({'valued':len(rows)})
if __name__=='__main__': main()
