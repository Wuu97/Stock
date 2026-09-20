import json, hashlib
from pathlib import Path
import pandas as pd
import duckdb

ROOT=Path('data/ridge_history_evidence'); OUT=ROOT/'full_five_results'; INITIAL=1_000_000.0
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def f(x): return float(x) if x is not None else None
def segment(nav, executions, start, end):
    x=nav[(nav.date>=pd.Timestamp(start))&(nav.date<=pd.Timestamp(end))].copy()
    prev=nav[nav.date<pd.Timestamp(start)].tail(1)
    base=float(prev.equity.iloc[0]) if len(prev) else INITIAL
    if len(x)==0: return None
    curve=pd.concat([pd.Series([base]),x.equity.reset_index(drop=True)],ignore_index=True)
    dd=curve/curve.cummax()-1
    ex=executions[(executions.date>=pd.Timestamp(start))&(executions.date<=pd.Timestamp(end))]
    gross=float(ex.gross.sum()) if len(ex) else 0.0
    fees=float((ex.commission+ex.stamp+ex.transfer).sum()) if len(ex) else 0.0
    return {'start':start,'end':end,'nav_opening':base,'nav_end':float(x.equity.iloc[-1]),'days':len(x),'return':float(x.equity.iloc[-1]/base-1),'max_drawdown':float(dd.min()),'executed_gross':gross,'turnover_gross_over_average_nav':gross/float(curve.mean()),'fees':fees,'fills':len(ex)}
def main():
    con=duckdb.connect('data/top50/quant.duckdb',read_only=True)
    periods={'full':('2023-09-12','2026-09-11'),'pre_223':('2023-09-12','2025-10-16'),'post_223':('2025-10-17','2026-09-11'),'2023_partial':('2023-09-12','2023-12-31'),'2024':('2024-01-01','2024-12-31'),'2025':('2025-01-01','2025-12-31'),'2026_partial':('2026-01-01','2026-09-11')}
    accounts={}
    for label in ('baseline','kdj','macd','pure','ridge'):
        a='competition_v4_ridge_3y_'+label
        nav=pd.DataFrame(con.execute('select trade_date,total_equity,cash_balance,securities_value from sim_nav_daily where account_id=? order by trade_date',[a]).fetchall(),columns=['date','equity','cash','sec']); nav.date=pd.to_datetime(nav.date); nav[['equity','cash','sec']]=nav[['equity','cash','sec']].astype(float)
        ex=pd.DataFrame(con.execute('select exec_id,trade_date,ticker,direction,gross_amount,commission,stamp_duty,transfer_fee from sim_executions where account_id=?',[a]).fetchall(),columns=['exec_id','date','ticker','direction','gross','commission','stamp','transfer']); ex.date=pd.to_datetime(ex.date); ex[['gross','commission','stamp','transfer']]=ex[['gross','commission','stamp','transfer']].astype(float)
        seg={k:segment(nav,ex,*v) for k,v in periods.items()}
        pre,post,full=seg['pre_223'],seg['post_223'],seg['full']
        compound=(1+pre['return'])*(1+post['return'])-1
        journal=con.execute('select coalesce(sum(debit_amount),0),coalesce(sum(credit_amount),0) from ledger_journal_entries where account_id=?',[a]).fetchone()
        final=nav.iloc[-1]
        accounts[label]={'segments':seg,'compound_check':{'pre_times_post':compound,'full':full['return'],'difference':compound-full['return']},'ledger_reconciliation':{'final_cash':f(final.cash),'final_securities_value':f(final.sec),'final_nav':f(final.equity),'cash_plus_securities_minus_nav':f(final.cash+final.sec-final.equity),'journal_debits':f(journal[0]),'journal_credits':f(journal[1]),'journal_imbalance':f(journal[0]-journal[1])}}
    # Ridge realized P&L and concentration use matched sell executions; remaining book cost is unrealized basis.
    a='competition_v4_ridge_3y_ridge'
    sells=pd.DataFrame(con.execute("select e.exec_id,e.trade_date,e.ticker,e.gross_amount,e.commission,e.stamp_duty,e.transfer_fee,d.total_cost_relieved from sim_executions e join sim_lot_disposal_events d on d.sell_event_id=e.exec_id where e.account_id=? and e.direction='SELL'",[a]).fetchall(),columns=['exec_id','date','ticker','gross','commission','stamp','transfer','cost']); sells[['gross','commission','stamp','transfer','cost']]=sells[['gross','commission','stamp','transfer','cost']].astype(float)
    sells['pnl_after_sell_costs']=sells.gross-sells.cost-sells.commission-sells.stamp-sells.transfer
    stock=sells.groupby('ticker').pnl_after_sell_costs.sum().sort_values()
    end_cost=con.execute("select coalesce(sum(book_cost_balance),0) from sim_positions_daily where account_id=? and trade_date='2026-09-11'",[a]).fetchone()[0]
    end_sec=con.execute("select securities_value from sim_nav_daily where account_id=? and trade_date='2026-09-11'",[a]).fetchone()[0]
    fees=con.execute('select coalesce(sum(commission+stamp_duty+transfer_fee),0) from sim_executions where account_id=?',[a]).fetchone()[0]
    concentration={'realized_matched_sell_pnl_after_sell_costs':f(sells.pnl_after_sell_costs.sum()),'unrealized_end_value_minus_book_cost':f(end_sec-end_cost),'total_execution_fees':f(fees),'top5_positive_sells':sells.nlargest(5,'pnl_after_sell_costs')[['ticker','date','pnl_after_sell_costs']].to_dict('records'),'top5_negative_sells':sells.nsmallest(5,'pnl_after_sell_costs')[['ticker','date','pnl_after_sell_costs']].to_dict('records'),'top5_ticker_contributors':stock.nlargest(5).to_dict(),'bottom5_ticker_contributors':stock.nsmallest(5).to_dict(),'largest5_share_of_positive_realized_pnl':f(sells.nlargest(5,'pnl_after_sell_costs').pnl_after_sell_costs.sum()/sells[sells.pnl_after_sell_costs>0].pnl_after_sell_costs.sum())}
    sched=json.loads((ROOT/'full_ridge_schedule_v2.json').read_text())['models']
    models=[]
    for m in sched:
        models.append({'model_id':m['model_id'],'training_decision_start':m['training_decision_dates'][0],'training_decision_end':m['training_decision_dates'][1],'label_availability_cutoff_date':m['label_availability_cutoff_date'],'prediction_start':m['prediction_dates'][0],'prediction_end':m['prediction_dates'][1],'model_sha256':m['model_sha256'],'model_config_sha256':m['model_config_sha256'],'training_rows':m['training_rows'],'ci_status':'INSUFFICIENT_SAMPLE' if m['model_id']=='full_w13' else 'REPORTED_IN_PREDICTION_SECTION'})
    old=json.loads((OUT/'competition_v4_ridge_3y_final_research_audit.json').read_text())
    report={'status':'CORRECTED_FINAL_RESEARCH_AUDIT','supersedes_for_methodology_only':'competition_v4_ridge_3y_final_research_audit.json','frozen_inputs':{'dataset':sha(ROOT/'ridge_v4_unified_dataset_v2.parquet'),'manifest':sha(ROOT/'ridge_v4_unified_dataset_v2.parquet.manifest.json'),'schedule':sha(ROOT/'full_ridge_schedule_v2.json'),'predictions':sha(ROOT/'full_ridge_predictions_v2.jsonl')},'corrections':{'segmented_return':'each segment uses prior trading day NAV (or initial capital) as opening capital; adjacent segments compound exactly','drawdown':'recomputed locally from opening NAV plus segment NAV curve','turnover':'executed gross notional divided by mean opening-plus-daily NAV; not a reuse of whole-period scorecard turnover'},'accounts':accounts,'ridge_profit_concentration':concentration,'ridge_model_windows':models,'prediction_quality_from_frozen_audit':old['prediction_quality'],'interpretation':'Ridge cumulative account return is +49.51%, but the full-period IC, Rank IC and Q5-Q1 HAC confidence intervals include zero. This does not establish a stable positive statistical signal.','limitations':['Realized P&L is computed from matched sell/disposal events and sale-side costs; purchase cost treatment remains governed by frozen lot accounting.','W13 has only two mature-label evaluation days and its confidence interval is explicitly not interpretable.','The 223-day overlap was previously observed and is not an independent validation sample.']}
    p=OUT/'competition_v4_ridge_3y_corrected_final_research_audit_v2.json'; p.write_text(json.dumps(report,indent=2,default=str)+'\n'); print(json.dumps({'path':str(p),'sha256':sha(p),'ridge_compound':accounts['ridge']['compound_check'],'ridge_concentration':concentration},indent=2,default=str))
if __name__=='__main__': main()
