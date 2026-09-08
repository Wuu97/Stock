"""Archive the HS300 benchmark used by the ML label as a separate immutable input."""

import argparse
import csv
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path

import tushare as ts

from quant_core.environment import load_env_file
from quant_core.snapshots import write_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--ticker", default="000300.SH")
    parser.add_argument("--output-dir", default="data/ml_history")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    if start > end:
        raise ValueError("--start-date must not be after --end-date")
    load_env_file(Path(".env"))
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ValueError("TUSHARE_TOKEN is not configured")
    frame = ts.pro_api(token).index_daily(
        ts_code=args.ticker, start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
        fields="ts_code,trade_date,close",
    )
    if not {"ts_code", "trade_date", "close"}.issubset(frame.columns) or frame.empty:
        raise RuntimeError("Tushare index_daily response is incomplete")
    rows = frame.to_dict("records")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{args.ticker.replace('.', '_')}_index_daily.raw.json"
    raw_path.write_text(json.dumps(rows, ensure_ascii=False, default=str, sort_keys=True), encoding="utf-8")
    csv_path = output_dir / f"{args.ticker.replace('.', '_')}_adjusted_closes.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=("trade_date", "ticker", "adj_close"))
        writer.writeheader()
        writer.writerows({"trade_date": f"{str(row['trade_date'])[:4]}-{str(row['trade_date'])[4:6]}-{str(row['trade_date'])[6:]}",
                          "ticker": args.ticker, "adj_close": str(row["close"])} for row in rows)
    manifest_path = output_dir / f"{args.ticker.replace('.', '_')}_index_daily.manifest.json"
    manifest_hash = write_manifest(manifest_path, {
        str(raw_path): sha256(raw_path.read_bytes()).hexdigest(), str(csv_path): sha256(csv_path.read_bytes()).hexdigest(),
    })
    print(json.dumps({"rows": len(rows), "csv": str(csv_path), "manifest": str(manifest_path), "manifest_sha256": manifest_hash}, ensure_ascii=False))


if __name__ == "__main__":
    main()
