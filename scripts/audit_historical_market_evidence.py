"""Read-only, fail-closed audit for Phase 2 historical market evidence."""

import argparse
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from quant_core.database import read_connection
from quant_core.historical_market_evidence import canonical_daily_evidence
from quant_core.snapshots import canonical_hash


SOURCE = "tushare_oos2020_history_daily_v1"


def _calendar_days(path: Path, start: date, end: date) -> list[date]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if payload.get("reference_type") != "HISTORICAL_TRADING_CALENDAR_REFERENCE" or path.stem.rsplit("_", 1)[-1] != expected[:20]:
        raise ValueError("frozen calendar reference is invalid")
    return [date.fromisoformat(value) for value in payload["trading_days"] if start <= date.fromisoformat(value) <= end]


def _manifest_raw(path: Path, expected_manifest_hash: str) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    check = canonical_hash({"artifacts": dict(sorted(manifest.get("artifacts", {}).items())),
                            "previous_hash": manifest.get("previous_hash", "")})
    if manifest.get("manifest_sha256") != expected_manifest_hash or check != expected_manifest_hash:
        raise ValueError("manifest hash is invalid")
    artifacts = manifest.get("artifacts", {})
    if len(artifacts) != 1:
        raise ValueError("market evidence manifest must have exactly one raw artifact")
    raw_path, raw_hash = next(iter(artifacts.items()))
    raw = Path(raw_path).read_bytes()
    if sha256(raw).hexdigest() != raw_hash:
        raise ValueError("raw artifact hash mismatch")
    return json.loads(raw)


def _bar_rows(bars):
    return {(bar.ticker, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.amount,
             bar.limit_up, bar.limit_down, bar.status)
            for bar in bars}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--calendar-reference", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--minimum-execution-market-cap", default="80000000000")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    expected_days = _calendar_days(Path(args.calendar_reference), start, end)
    if not expected_days:
        raise ValueError("no frozen trading dates are in scope")
    minimum = Decimal(args.minimum_execution_market_cap)
    rebuilt_bars = rebuilt_caps = 0
    with read_connection(args.db) as connection:
        rows = connection.execute(
            "SELECT market_snapshot_id,trade_date,manifest_path,manifest_sha256 FROM market_data_snapshots "
            "WHERE source_channel=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date,market_snapshot_id",
            [SOURCE, start, end],
        ).fetchall()
        if len(rows) != len(expected_days) or [row[1] for row in rows] != expected_days:
            raise ValueError("market snapshot dates do not exactly match frozen calendar coverage")
        for snapshot_id, trade_date, manifest_path, manifest_hash in rows:
            expected_id = f"oos2020_tushare_market_{trade_date:%Y%m%d}"
            if snapshot_id != expected_id:
                raise ValueError(f"unexpected snapshot id for {trade_date}")
            evidence = _manifest_raw(Path(manifest_path), manifest_hash)
            request = evidence.get("request_identity", {})
            if request != {"provider": "tushare", "trade_date": trade_date.isoformat(),
                           "endpoints": ["daily", "daily_basic", "adj_factor", "stk_limit"]}:
                raise ValueError(f"raw request identity mismatch for {trade_date}")
            responses = evidence.get("responses", {})
            bars, caps, canonical = canonical_daily_evidence(
                trade_date, responses.get("daily", []), responses.get("daily_basic", []),
                responses.get("adj_factor", []), responses.get("stk_limit", []), minimum,
            )
            if canonical != evidence:
                raise ValueError(f"raw evidence is not canonical for {trade_date}")
            database_bars = connection.execute(
                "SELECT ticker,open,high,low,close,volume,amount,limit_up,limit_down,status FROM daily_bars "
                "WHERE market_snapshot_id=?", [snapshot_id],
            ).fetchall()
            actual_bars = {(ticker, Decimal(str(open_)), Decimal(str(high)), Decimal(str(low)), Decimal(str(close)), volume,
                            Decimal(str(amount)), None if up is None else Decimal(str(up)),
                            None if down is None else Decimal(str(down)), status)
                           for ticker, open_, high, low, close, volume, amount, up, down, status in database_bars}
            if _bar_rows(bars) != actual_bars or len(bars) != len(database_bars):
                raise ValueError(f"normalized daily bars differ from raw evidence for {trade_date}")
            cap_id = f"oos2020_tushare_cap_{trade_date:%Y%m%d}"
            database_caps = connection.execute(
                "SELECT ticker,total_market_cap FROM market_cap_values WHERE market_cap_snapshot_id=?", [cap_id],
            ).fetchall()
            actual_caps = {ticker: Decimal(str(value)) for ticker, value in database_caps}
            if caps != actual_caps or len(caps) != len(database_caps):
                raise ValueError(f"normalized market caps differ from raw evidence for {trade_date}")
            rebuilt_bars += len(bars); rebuilt_caps += len(caps)
        universes = connection.execute(
            "SELECT count(*) FROM universe_snapshots WHERE as_of_trade_date BETWEEN ? AND ?", [start, end],
        ).fetchone()[0]
    print(json.dumps({"source": SOURCE, "calendar_days": len(expected_days), "snapshot_coverage": "PASS",
                      "raw_artifact_integrity": "PASS", "raw_to_normalized_bars": "PASS",
                      "raw_to_normalized_market_caps": "PASS", "rebuilt_bars": rebuilt_bars,
                      "rebuilt_market_caps": rebuilt_caps, "universe_snapshots_created": universes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
