"""Import one user-recorded opening cash portfolio from a JSON artifact."""

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from quant_core.database import writer_connection

from quant_core.portfolio_import import OpeningHolding, import_opening_portfolio


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--portfolio", required=True, help="JSON with account_id, account_name, cash_balance, as_of_date, holdings.")
    args = parser.parse_args()
    source = Path(args.portfolio)
    payload = json.loads(source.read_text(encoding="utf-8"))
    holdings = tuple(OpeningHolding(str(item["ticker"]), int(item["shares"]), Decimal(str(item["unit_cost"])))
                     for item in payload["holdings"])
    connection = writer_connection(args.db, transaction=False)
    try:
        opening_net_assets = payload.get("opening_net_assets")
        opening_snapshot_id = import_opening_portfolio(
            connection, str(payload["account_id"]), str(payload["account_name"]), Decimal(str(payload["cash_balance"])),
            date.fromisoformat(payload["as_of_date"]), holdings, source,
            Decimal(str(opening_net_assets)) if opening_net_assets is not None else None,
        )
    finally:
        connection.close()
    print(json.dumps({"account_id": payload["account_id"], "opening_snapshot_id": opening_snapshot_id,
                      "holdings": len(holdings)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
