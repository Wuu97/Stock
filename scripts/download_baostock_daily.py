"""Download selected BaoStock symbols into the project's validated daily CSV format."""

import argparse
import csv
from datetime import date
from pathlib import Path

from quant_core.baostock_source import download_daily


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", required=True, help="Comma-separated BaoStock codes, e.g. sh.600000,sz.000001")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bars = list(download_daily(args.codes.split(","), date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=[
            "trade_date", "ticker", "open", "high", "low", "close", "volume", "amount", "limit_up", "limit_down", "status",
        ])
        writer.writeheader()
        for bar in bars:
            writer.writerow({
                "trade_date": bar.trade_date, "ticker": bar.ticker, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume, "amount": bar.amount,
                "limit_up": "", "limit_down": "", "status": bar.status,
            })


if __name__ == "__main__":
    main()
