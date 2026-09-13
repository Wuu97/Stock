"""Import one user-recorded opening margin portfolio from an auditable JSON artifact."""

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from quant_core.database import writer_connection

from quant_core.margin import OpeningMarginDebt
from quant_core.portfolio_import import OpeningHolding, import_opening_margin_portfolio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--portfolio", required=True)
    args = parser.parse_args()
    source = Path(args.portfolio)
    payload = json.loads(source.read_text(encoding="utf-8"))
    holdings = tuple(OpeningHolding(str(item["ticker"]), int(item["shares"]), Decimal(str(item["unit_cost"]))) for item in payload["holdings"])
    debts = tuple(OpeningMarginDebt(str(item["ticker"]), Decimal(str(item["opening_amount"])), Decimal(str(item["outstanding_balance"])))
                  for item in payload["financing_debts"])
    connection = writer_connection(args.db, transaction=False)
    try:
        opening_net_assets = payload.get("opening_net_assets")
        snapshot = import_opening_margin_portfolio(connection, str(payload["account_id"]), str(payload["account_name"]),
                                                    Decimal(str(payload["cash_balance"])), date.fromisoformat(payload["as_of_date"]),
                                                    holdings, source, Decimal(str(payload["annual_financing_rate"])), debts,
                                                    Decimal(str(opening_net_assets)) if opening_net_assets is not None else None)
    finally:
        connection.close()
    print(json.dumps({"account_id": payload["account_id"], "opening_snapshot_id": snapshot,
                      "holdings": len(holdings), "financing_debts": len(debts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
