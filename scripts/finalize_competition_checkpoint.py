"""Finalize one completed Competition replay without replaying orders."""
import argparse, csv, json
from datetime import date, datetime, timezone
from decimal import Decimal
from quant_core.database import writer_connection
from quant_core.experiments import BenchmarkClose, calculate_metrics
from quant_core.strategy_scorecard import CompetitionProfile, StrategyScorecardStore

def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',required=True);p.add_argument('--experiment-id',required=True);p.add_argument('--account-id',required=True);p.add_argument('--profile-id',required=True);p.add_argument('--profile-version',required=True);p.add_argument('--profile-hash',required=True);p.add_argument('--benchmark-csv',required=True);p.add_argument('--benchmark-ticker',default='000300.SH');a=p.parse_args()
 with writer_connection(a.db) as c:
  e=c.execute('select account_id,spec_json from strategy_experiments where experiment_id=?',[a.experiment_id]).fetchone()
  pr=c.execute('select profile_json,profile_sha256 from competition_profiles where competition_profile_id=? and competition_profile_version=?',[a.profile_id,a.profile_version]).fetchone()
  if not e or e[0]!=a.account_id or not pr or pr[1]!=a.profile_hash: raise ValueError('experiment/profile lineage mismatch')
  spec=json.loads(e[1]); start,end=date.fromisoformat(spec['start_date']),date.fromisoformat(spec['end_date'])
  nav=c.execute('select count(*),count(distinct trade_date),min(trade_date),max(trade_date) from sim_nav_daily where account_id=?',[a.account_id]).fetchone()
  expected=c.execute("select count(distinct trade_date) from market_data_snapshots where source_channel='tushare_history_daily' and trade_date between ? and ?",[start,end]).fetchone()[0]
  if nav!=(expected,expected,start,end) or c.execute("select 1 from sim_order_intents where account_id=? and order_status='PENDING'",[a.account_id]).fetchone(): raise ValueError('replay is not complete')
  profile=CompetitionProfile(a.profile_id,a.profile_version,json.loads(pr[0])); rows=csv.DictReader(open(a.benchmark_csv)); bench=[BenchmarkClose(date.fromisoformat(r['trade_date']),Decimal(r['adj_close'])) for r in rows if r['ticker']==a.benchmark_ticker]
  raise RuntimeError('legacy finalize is disabled; use derived evaluation lineage')
if __name__=='__main__': main()
