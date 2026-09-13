"""Immutable listing-reference snapshots for point-in-time universe eligibility."""

from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Tuple


def _date_or_none(value: object) -> Optional[date]:
    text = str(value).strip()
    if text.lower() in {"", "none", "nan", "nat"}:
        return None
    if len(text) != 8 or not text.isdigit():
        raise ValueError("stock_basic date is invalid")
    return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")


def listing_rows(rows: Iterable[Mapping[str, object]]) -> Tuple[tuple[str, date, Optional[date], str], ...]:
    """Normalize Tushare ``stock_basic`` facts without inferring historical status."""
    normalized = {}
    for row in rows:
        ticker = str(row.get("ts_code", "")).strip()
        list_status = str(row.get("list_status", "")).strip()
        listed, delisted = _date_or_none(row.get("list_date")), _date_or_none(row.get("delist_date"))
        if not ticker or listed is None or list_status not in {"L", "D", "P"}:
            raise ValueError("stock_basic row has incomplete listing eligibility facts")
        normalized[ticker] = (
            ticker,
            listed,
            delisted,
            list_status,
        )
    return tuple(sorted(normalized.values()))


class SecurityEligibilityStore:
    def __init__(self, connection):
        self.connection = connection

    def store_listing_snapshot(self, snapshot_id: str, source_channel: str, raw_artifact_path: str,
                               raw_artifact_sha256: str, received_at: datetime,
                               rows: Iterable[tuple[str, date, Optional[date], str]], created_at: datetime) -> int:
        values = tuple(rows)
        if (not snapshot_id or not source_channel or not raw_artifact_path or len(raw_artifact_sha256) != 64):
            raise ValueError("listing snapshot provenance is incomplete")
        self.connection.execute(
            "INSERT INTO security_listing_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            [snapshot_id, source_channel, raw_artifact_path, raw_artifact_sha256, received_at, created_at],
        )
        if values:
            self.connection.executemany(
                "INSERT INTO security_listing_values VALUES (?, ?, ?, ?, ?)",
                [(snapshot_id, ticker, list_date, delist_date, status) for ticker, list_date, delist_date, status in values],
            )
        return len(values)
