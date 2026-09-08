"""Create a new daily-bar CSV enriched with historical Tushare price limits."""

import argparse
import csv
from os import environ
from pathlib import Path

from quant_core.environment import load_env_file
from quant_core.market_data import read_daily_csv
from quant_core.tushare_source import fetch_daily_limits, merge_daily_limits, missing_limits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--token-env", default="TUSHARE_TOKEN")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--require-tickers", default="", help="Comma-separated tickers that must receive limits")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))
    token = environ.get(args.token_env)
    if not token:
        raise RuntimeError("set the Tushare token in the configured environment variable")
    bars = read_daily_csv(Path(args.input))
    enriched = merge_daily_limits(bars, fetch_daily_limits(token, [bar.trade_date for bar in bars]))
    required = [ticker for ticker in args.require_tickers.split(",") if ticker]
    missing = missing_limits(enriched, required)
    if missing:
        preview = ", ".join(f"{day}:{ticker}" for day, ticker in missing[:5])
        raise RuntimeError(f"Tushare limits are incomplete for required tickers: {preview}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=[
            "trade_date", "ticker", "open", "high", "low", "close", "volume", "amount", "limit_up", "limit_down", "status",
        ])
        writer.writeheader()
        for bar in enriched:
            writer.writerow({
                "trade_date": bar.trade_date, "ticker": bar.ticker, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume, "amount": bar.amount,
                "limit_up": bar.limit_up or "", "limit_down": bar.limit_down or "", "status": bar.status,
            })


if __name__ == "__main__":
    main()
