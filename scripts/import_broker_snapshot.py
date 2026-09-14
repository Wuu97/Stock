"""Append one verified, composite broker snapshot; no broker connection or order creation."""
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from quant_core.database import writer_connection

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',required=True); p.add_argument('--input',required=True); a=p.parse_args()
    data=json.load(open(a.input)); now=datetime.now(timezone.utc); sid=str(uuid4())
    required = ("account_id", "observed_at", "collateral_assets", "debt_balance", "financing_limit",
                "financing_used", "available_cash", "available_margin", "maintenance_ratio", "source_reference", "holdings")
    missing = [field for field in required if data.get(field) is None]
    if missing:
        raise ValueError("broker snapshot is incomplete: " + ", ".join(missing))
    if not isinstance(data["source_reference"], (str, list)):
        raise ValueError("source_reference must identify the three screenshot artifacts")
    reference = json.dumps(data["source_reference"], ensure_ascii=False) if isinstance(data["source_reference"], list) else data["source_reference"]
    with writer_connection(a.db) as c:
        c.execute((Path(__file__).resolve().parent.parent / "sql" / "schema.sql").read_text(encoding="utf-8"))
        # Supports databases created before cash/margin availability was captured.
        c.execute("ALTER TABLE external_account_snapshots ADD COLUMN IF NOT EXISTS available_cash DECIMAL(20,4)")
        c.execute("ALTER TABLE external_account_snapshots ADD COLUMN IF NOT EXISTS available_margin DECIMAL(20,4)")
        c.execute("ALTER TABLE external_account_snapshots ADD COLUMN IF NOT EXISTS accrued_financing_interest DECIMAL(20,4)")
        c.execute("INSERT INTO external_account_snapshots (snapshot_id, account_id, observed_at, collateral_assets, debt_balance, financing_limit, financing_used, accrued_financing_interest, available_cash, available_margin, maintenance_ratio, chinext_star_concentration, source_reference, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [sid,data['account_id'],data['observed_at'],data['collateral_assets'],data['debt_balance'],data['financing_limit'],data['financing_used'],data.get('accrued_financing_interest', 0),data['available_cash'],data['available_margin'],data['maintenance_ratio'],data.get('chinext_star_concentration'),reference,now])
        c.executemany("INSERT INTO external_account_snapshot_holdings VALUES (?,?,?,?,?,?)", [(sid,x['ticker'],x['shares'],x['sellable_shares'],x['unit_cost'],x['market_price']) for x in data['holdings']])
    print(json.dumps({'snapshot_id':sid,'holdings':len(data['holdings'])}))
if __name__=='__main__': main()
