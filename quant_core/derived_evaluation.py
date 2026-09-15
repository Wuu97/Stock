"""Immutable evaluation lineage over an already completed replay."""
import json
from hashlib import sha256

def execution_fingerprint(profile, strategy):
    p=dict(profile); p.pop('benchmark_binding',None); p.pop('evaluation_methodology',None)
    return sha256(json.dumps({'profile':p,'strategy':strategy},sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate_replay(connection, account_id, start, end):
    n,u,lo,hi=connection.execute('select count(*),count(distinct trade_date),min(trade_date),max(trade_date) from sim_nav_daily where account_id=?',[account_id]).fetchone()
    if n!=u or lo!=start or hi!=end or connection.execute("select 1 from sim_order_intents where account_id=? and order_status='PENDING'",[account_id]).fetchone(): raise ValueError('source replay is incomplete')
