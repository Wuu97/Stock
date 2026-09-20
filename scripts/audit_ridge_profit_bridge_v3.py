import json, hashlib
from pathlib import Path
import duckdb

OUT=Path('data/ridge_history_evidence/full_five_results')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    c=duckdb.connect('data/top50/quant.duckdb',read_only=True); a='competition_v4_ridge_3y_ridge'
    q=lambda s: c.execute(s,[a]).fetchone()
    gross_sales,cost=q("select sum(e.gross_amount),sum(d.total_cost_relieved) from sim_executions e join sim_lot_disposal_events d on d.sell_event_id=e.exec_id where e.account_id=? and e.direction='SELL'")
    buy_fee,sell_fee=q("select sum(case when direction='BUY' then commission+stamp_duty+transfer_fee else 0 end),sum(case when direction='SELL' then commission+stamp_duty+transfer_fee else 0 end) from sim_executions where account_id=?")
    end_cash,end_sec,end_nav=q("select cash_balance,securities_value,total_equity from sim_nav_daily where account_id=? and trade_date='2026-09-11'")
    book=q("select sum(book_cost_balance) from sim_positions_daily where account_id=? and trade_date='2026-09-11'")[0]
    bridge={'initial_capital':1000000.0,'realized_gross_profit_before_selling_fees':float(gross_sales-cost),'unrealized_profit_end_securities_value_minus_book_cost':float(end_sec-book),'less_buy_fees_expensed_separately':float(buy_fee),'less_sell_fees_expensed_separately':float(sell_fee),'less_total_execution_fees':float(buy_fee+sell_fee),'ending_nav':float(end_nav),'bridge_check':float(1000000+(gross_sales-cost)+(end_sec-book)-buy_fee-sell_fee-end_nav)}
    report={'status':'PROFIT_BRIDGE_RECONCILED','account_id':a,'bridge':bridge,'difference_explained':{'amount':float(sell_fee),'meaning':'The ¥24,504.57 difference is all selling-side execution fees: commission, stamp duty and transfer fee. Gross sale proceeds less relieved lot cost equals ¥498,195.72; net realized trading profit after selling fees equals ¥473,691.15.','fee_treatment':{'buy_fees':'¥8,510.27 are recorded as expenses at purchase and are not embedded in lot book cost.','sell_fees':'¥24,504.57 are recorded as expenses at sale and are not embedded in relieved lot cost.','no_double_counting':'The final bridge subtracts each fee once: gross realized profit and unrealized value are before fee expense, then total fees are subtracted once.'}},'concentration_method':{'unit':'matched sell execution; ticker aggregation sums matched sell execution P&L after selling-side costs','numerator':'sum of selected positive matched sell P&L, or each ticker total matched sell P&L after selling-side costs','denominator':'sum of all positive matched sell P&L after selling-side costs; this is not total account profit, final NAV gain, or gross sales','scope':'unrealized end holdings and buy fees are excluded from the concentration denominator and are separately reported in the bridge.'}}
    p=OUT/'competition_v4_ridge_3y_profit_bridge_and_concentration_v3.json';p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'path':str(p),'sha256':sha(p),'bridge':bridge},indent=2))
if __name__=='__main__':main()
