"""Immutable persistence for manually reviewed company event exposures."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional


@dataclass(frozen=True)
class EventExposure:
    ticker: str
    taxonomy_version: str
    industry_code: str
    valid_from: date
    valid_to: Optional[date]
    exposure_weight: Decimal
    rationale: str
    source_reference: str
    reviewed_by: str

    def __post_init__(self):
        if not self.ticker or not self.taxonomy_version or not self.industry_code:
            raise ValueError("event exposure requires ticker, taxonomy version and industry code")
        if not Decimal("0") < self.exposure_weight <= Decimal("1"):
            raise ValueError("event exposure weight must be in (0, 1]")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("event exposure valid_to cannot precede valid_from")
        if not all((self.rationale.strip(), self.source_reference.strip(), self.reviewed_by.strip())):
            raise ValueError("event exposure requires rationale, source_reference and reviewed_by")


def store_exposures(connection, exposure_version: str, exposures: Iterable[EventExposure], created_at: datetime) -> int:
    """Append reviewed exposures; reject duplicate versioned observations."""
    if not exposure_version.strip() or created_at.tzinfo is None:
        raise ValueError("exposure version and timezone-aware created_at are required")
    rows = tuple(exposures)
    connection.executemany(
        "INSERT INTO security_event_exposures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(exposure_version, item.ticker, item.taxonomy_version, item.industry_code, item.valid_from, item.valid_to,
          item.exposure_weight, item.rationale, item.source_reference, item.reviewed_by, created_at) for item in rows],
    )
    return len(rows)
