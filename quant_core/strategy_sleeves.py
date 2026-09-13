"""Versioned, auditable assignment of securities to investment-horizon sleeves."""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4


@dataclass(frozen=True)
class SleeveAssignment:
    ticker: str
    sleeve: str
    review_interval_days: int
    max_holding_days: int
    assignment_source: str
    rationale: str


def load_sleeve_config(path: Path) -> tuple[Mapping[str, object], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != "strategy_sleeves_v1":
        raise ValueError("unsupported strategy sleeve configuration")
    if not isinstance(payload.get("sleeves"), dict) or not isinstance(payload.get("ticker_overrides"), dict):
        raise ValueError("strategy sleeve configuration is incomplete")
    return payload, sha256(raw).hexdigest()


def classify_tickers(connection, tickers, config: Mapping[str, object]) -> tuple[SleeveAssignment, ...]:
    sleeves, overrides = config["sleeves"], config["ticker_overrides"]
    result = []
    for ticker in sorted(set(tickers)):
        override = overrides.get(ticker)
        if override:
            sleeve, source, rationale = str(override["sleeve"]), "CONFIG_OVERRIDE", str(override["rationale"])
        else:
            row = connection.execute("SELECT instrument_type FROM security_master WHERE ticker = ?", [ticker]).fetchone()
            sleeve = "TREND" if row and row[0] == "ETF" else "TACTICAL"
            source, rationale = "INSTRUMENT_DEFAULT", "ETF 默认趋势仓" if sleeve == "TREND" else "个股默认战术仓"
        definition = sleeves.get(sleeve)
        if not definition:
            raise ValueError(f"unknown sleeve {sleeve} for {ticker}")
        result.append(SleeveAssignment(ticker, sleeve, int(definition["review_interval_days"]),
                                       int(definition["max_holding_days"]), source, rationale))
    return tuple(result)


def store_sleeve_snapshot(connection, group_name: str, as_of_trade_date: date, config_path: Path,
                          config_sha256: str, assignments, created_at: datetime) -> str:
    snapshot_id = str(uuid4())
    connection.execute("INSERT INTO strategy_sleeve_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                       [snapshot_id, group_name, as_of_trade_date, str(config_path), config_sha256, created_at])
    connection.executemany("INSERT INTO strategy_sleeve_assignments VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (snapshot_id, item.ticker, item.sleeve, item.review_interval_days, item.max_holding_days,
         item.assignment_source, item.rationale) for item in assignments
    ])
    return snapshot_id
