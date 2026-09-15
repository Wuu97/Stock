"""Archive a small immutable SSE open-day calendar reference for PIT eligibility."""

import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

import tushare as ts

from quant_core.environment import load_env_file


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--artifact-dir", default="data/security_eligibility")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end:
        raise ValueError("start-date must not be after end-date")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    frame = ts.pro_api(token).trade_cal(exchange="SSE", start_date=start.strftime("%Y%m%d"),
                                         end_date=end.strftime("%Y%m%d"), is_open="1")
    days = sorted(str(value) for value in frame["cal_date"].tolist())
    payload = {"reference_type": "HISTORICAL_TRADING_CALENDAR_REFERENCE", "reference_version": "historical_trading_calendar_reference_v1",
               "source": "tushare_trade_cal_sse_open_days_v1", "exchange": "SSE", "start_date": args.start_date,
               "end_date": args.end_date, "trading_days": [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in days],
               "received_at": datetime.now(timezone.utc).isoformat()}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = sha256(raw.encode()).hexdigest()
    directory = Path(args.artifact_dir); directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"historical_trading_calendar_reference_{digest[:20]}.json"
    path.write_text(raw + "\n", encoding="utf-8")
    print(json.dumps({"calendar_reference": str(path), "sha256": digest, "trading_day_count": len(days)}))


if __name__ == "__main__":
    main()
