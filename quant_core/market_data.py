"""Offline CSV ingestion and read-only daily-bar access for the baseline workflow."""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, List

from .models import DayBar


REQUIRED_COLUMNS = {
    "trade_date", "ticker", "open", "high", "low", "close", "volume", "amount",
    "limit_up", "limit_down", "status",
}


def read_daily_csv(path: Path) -> List[DayBar]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise ValueError("daily CSV is missing required columns")
        return [
            DayBar(
                trade_date=date.fromisoformat(row["trade_date"]), ticker=row["ticker"],
                open=Decimal(row["open"]), high=Decimal(row["high"]), low=Decimal(row["low"]),
                close=Decimal(row["close"]), volume=int(row["volume"]), amount=Decimal(row["amount"]),
                limit_up=_optional_decimal(row["limit_up"]), limit_down=_optional_decimal(row["limit_down"]),
                status=row["status"],
            )
            for row in reader
        ]


def _optional_decimal(value: str):
    return Decimal(value) if value.strip() else None


def validate_bar(bar: DayBar) -> DayBar:
    """Reject malformed vendor data; this function never fills or derives missing fields."""
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(not price.is_finite() or price < 0 for price in prices):
        raise ValueError(f"invalid OHLC price for {bar.ticker} on {bar.trade_date}")
    if bar.status == "TRADING":
        if any(price <= 0 for price in prices) or bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise ValueError(f"invalid trading OHLC relationship for {bar.ticker} on {bar.trade_date}")
    if bar.volume < 0 or not bar.amount.is_finite() or bar.amount < 0:
        raise ValueError(f"invalid volume or amount for {bar.ticker} on {bar.trade_date}")
    if (bar.limit_up is None) != (bar.limit_down is None):
        raise ValueError(f"incomplete price limits for {bar.ticker} on {bar.trade_date}")
    if bar.limit_up is not None and (
        not bar.limit_up.is_finite() or not bar.limit_down.is_finite() or bar.limit_down <= 0 or bar.limit_up < bar.limit_down
    ):
        raise ValueError(f"invalid price limits for {bar.ticker} on {bar.trade_date}")
    return bar


class MarketDataStore:
    def __init__(self, connection):
        self.connection = connection

    def store_bars(self, snapshot_id: str, bars: Iterable[DayBar]) -> None:
        rows = [
            (snapshot_id, bar.trade_date, bar.ticker, bar.open, bar.high, bar.low, bar.close,
             bar.volume, bar.amount, bar.limit_up, bar.limit_down, bar.status)
            for bar in (validate_bar(item) for item in bars)
        ]
        if not rows:
            raise ValueError("daily snapshot cannot be empty")
        self.connection.executemany("INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def load_bars(self, snapshot_id: str) -> List[DayBar]:
        rows = self.connection.execute(
            "SELECT trade_date, ticker, open, high, low, close, volume, amount, limit_up, limit_down, status "
            "FROM daily_bars WHERE market_snapshot_id = ? ORDER BY trade_date, ticker", [snapshot_id]
        ).fetchall()
        return [DayBar(*row) for row in rows]

    def load_bars_many(self, snapshot_ids: Iterable[str]) -> List[DayBar]:
        """Combine immutable snapshots; later IDs take precedence for duplicate date/ticker rows."""
        combined = {}
        for snapshot_id in snapshot_ids:
            for bar in self.load_bars(snapshot_id):
                combined[(bar.trade_date, bar.ticker)] = bar
        return [combined[key] for key in sorted(combined)]
