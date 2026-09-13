"""Vendor-supplied security display names kept separate from immutable trading facts."""

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Mapping, Tuple


def security_name_rows(rows: Iterable[Mapping[str, object]]) -> Tuple[tuple[str, str], ...]:
    """Validate and deterministically normalize Tushare ``stock_basic`` rows."""
    names = {}
    for row in rows:
        ticker, name = str(row.get("ts_code", "")).strip(), str(row.get("name", "")).strip()
        if not ticker or not name:
            raise ValueError("security master row requires ts_code and name")
        names[ticker] = name
    return tuple(sorted(names.items()))


class SecurityMasterStore:
    def __init__(self, connection):
        self.connection = connection

    def upsert(self, rows: Iterable[tuple[str, str]], source_channel: str, raw_artifact_path: str,
               raw_artifact_sha256: str, received_at: datetime, updated_at: datetime,
               instrument_type: str = "A_SHARE", settlement_cycle: str = "T1", board_lot: int = 100,
               price_tick: Decimal = Decimal("0.01"), price_limit_ratio: Decimal = Decimal("0.10"),
               sell_stamp_duty_rate: Decimal = Decimal("0.00050")) -> int:
        normalized = tuple(rows)
        if (not source_channel or not raw_artifact_path or len(raw_artifact_sha256) != 64 or
                instrument_type not in {"A_SHARE", "ETF"} or settlement_cycle not in {"T0", "T1"} or
                board_lot <= 0 or price_tick <= 0 or sell_stamp_duty_rate < 0):
            raise ValueError("security master provenance is incomplete")
        if normalized:
            self.connection.executemany(
                "INSERT OR REPLACE INTO security_master VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(ticker, name, source_channel, raw_artifact_path, raw_artifact_sha256, received_at, updated_at,
                  instrument_type, settlement_cycle, board_lot, price_tick, price_limit_ratio, sell_stamp_duty_rate)
                 for ticker, name in normalized],
            )
        return len(normalized)
