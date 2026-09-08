"""Settle all frozen recommendations for one account and one trading date."""

import argparse
from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json

import duckdb

from quant_core.daily_settlement import settle_frozen_buys
from quant_core.models import FeeModel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--market-snapshot-id", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--next-trading-date", required=True)
    parser.add_argument("--shares", type=int, default=100)
    parser.add_argument("--commission-rate", default="0.00025")
    parser.add_argument("--min-commission", default="5")
    parser.add_argument("--stamp-duty-rate", default="0.0005")
    parser.add_argument("--transfer-fee-rate", default="0.00001")
    parser.add_argument("--slippage-rate", default="0.001")
    args = parser.parse_args()

    fee = FeeModel(
        "cost_a_share_2026_v1", Decimal(args.commission_rate), Decimal(args.min_commission),
        Decimal(args.stamp_duty_rate), Decimal(args.transfer_fee_rate), Decimal(args.slippage_rate),
    )
    connection = duckdb.connect(args.db)
    outcomes = settle_frozen_buys(
        connection, args.account_id, args.market_snapshot_id, date.fromisoformat(args.trade_date),
        date.fromisoformat(args.next_trading_date), args.shares, fee,
    )
    connection.close()
    print(json.dumps([asdict(outcome) for outcome in outcomes], ensure_ascii=False))
