"""Immutable official trading-status evidence used by settlement and valuation."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional


@dataclass(frozen=True)
class TradingStatusEvent:
    ticker: str
    trade_date: date
    status_code: str
    suspend_timing: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status_code not in {"SUSPENDED", "RESUMED"}:
            raise ValueError("trading status must be SUSPENDED or RESUMED")


class TradingStatusStore:
    def __init__(self, connection):
        self.connection = connection

    def store_snapshot(self, snapshot_id: str, source_channel: str, start_date: date, end_date: date,
                       raw_artifact_path: str, raw_artifact_sha256: str, received_at: datetime,
                       events: Iterable[TradingStatusEvent], created_at: datetime) -> None:
        if start_date > end_date:
            raise ValueError("trading-status snapshot has an invalid date range")
        rows = _daily_status_events(events)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            self.connection.execute("INSERT INTO trading_status_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                snapshot_id, source_channel, start_date, end_date, raw_artifact_path, raw_artifact_sha256,
                received_at, created_at,
            ])
            if rows:
                self.connection.executemany("INSERT INTO trading_status_events VALUES (?, ?, ?, ?, ?)", [
                    (snapshot_id, event.ticker, event.trade_date, event.status_code, event.suspend_timing) for event in rows
                ])
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def suspended_tickers_by_date(self, source_channel: str, start_date: date, end_date: date) -> dict[date, set[str]]:
        rows = self.connection.execute(
            "SELECT e.trade_date, e.ticker FROM trading_status_events e "
            "JOIN trading_status_snapshots s ON s.trading_status_snapshot_id = e.trading_status_snapshot_id "
            "WHERE s.source_channel = ? AND e.status_code = 'SUSPENDED' "
            "AND e.trade_date BETWEEN ? AND ? ORDER BY e.trade_date, e.ticker",
            [source_channel, start_date, end_date],
        ).fetchall()
        result: dict[date, set[str]] = {}
        for trade_date, ticker in rows:
            result.setdefault(trade_date, set()).add(ticker)
        return result


def _daily_status_events(events: Iterable[TradingStatusEvent]) -> tuple[TradingStatusEvent, ...]:
    """Collapse intraday duplicate records conservatively for a daily-bar model."""
    by_key: dict[tuple[str, date], TradingStatusEvent] = {}
    for event in events:
        key = (event.ticker, event.trade_date)
        current = by_key.get(key)
        if current is None or event.status_code == "SUSPENDED":
            by_key[key] = event
    return tuple(by_key[key] for key in sorted(by_key))
