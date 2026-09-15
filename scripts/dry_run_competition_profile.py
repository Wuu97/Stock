import argparse,json
from datetime import date
from quant_core.database import read_connection
from quant_core.competition_profile_binding import universe_binding,market_binding,benchmark_binding,complete_profile,semantic_hash,canonical_json

def main():
 p=argparse.ArgumentParser();
 for n in ('db','universe-group','start-date','end-date','calendar-reference','market-source','benchmark-id','benchmark-source','benchmark-version','benchmark-file','replay-json'): p.add_argument('--'+n,required=True)
 p.add_argument('--profile-id',required=True);p.add_argument('--profile-version',required=True);a=p.parse_args(); start,end=date.fromisoformat(a.start_date),date.fromisoformat(a.end_date)
 with read_connection(a.db) as c:
  profile=complete_profile(universe_binding(c,a.universe_group,start,end,a.calendar_reference),market_binding(c,a.market_source,start,end),benchmark_binding(a.benchmark_id,a.benchmark_source,a.benchmark_version,a.benchmark_file),json.loads(a.replay_json),{"evaluation_stage":"BACKTEST","methodology_version":"strategy_scorecard_v1_60d_rolling_sharpe","rolling_sharpe_window_days":60,"sample_status_methodology_version":"sample_status_v1_min_30_round_trip_trades"})
 print(json.dumps({"competition_profile_id":a.profile_id,"competition_profile_version":a.profile_version,"competition_profile_hash":semantic_hash(profile),"canonical_profile":profile,"strategy_independent":True},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__': main()
