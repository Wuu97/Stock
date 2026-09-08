"""Run a free, walk-forward K-line signal study from a local daily-bar CSV."""

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from quant_core.market_data import read_daily_csv
from quant_core.signal_study import run_baseline_signal_study, summarize_signal_study
from quant_core.strategy import BaselineConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--tickers", required=True, help="Comma-separated eligible stock tickers; do not include benchmarks")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--volume-multiple", type=float, default=1.0)
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    observations = run_baseline_signal_study(
        read_daily_csv(csv_path),
        args.tickers.split(","),
        date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date),
        args.lookback_days,
        BaselineConfig("momentum_trend_v1", args.volume_multiple, args.top_n),
    )
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": sha256(csv_path.read_bytes()).hexdigest(),
        "parameters": vars(args) | {"horizons": [1, 5, 20]},
        "summary": summarize_signal_study(observations),
        "observations": [asdict(row) for row in observations],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], default=str, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
