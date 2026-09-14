"""Point-in-time intraday and auction data used by execution policies.

These stores do not infer quotes from daily OHLC data.  Missing data stays
missing so callers can fail closed instead of manufacturing executable prices.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional, Sequence, Tuple


@dataclass(frozen=True)
class IntradayBar:
    ticker: str
    bar_start_at: datetime
    bar_end_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal
    status: str = "TRADING"


@dataclass(frozen=True)
class AuctionQuoteLevel:
    side: str
    level_number: int
    price: Optional[Decimal]
    quantity: int


@dataclass(frozen=True)
class AuctionQuote:
    auction_snapshot_id: str
    trade_date: object
    ticker: str
    captured_at: datetime
    source_channel: str
    source_published_at: datetime
    received_at: datetime
    raw_artifact_path: str
    raw_artifact_sha256: str
    levels: Tuple[AuctionQuoteLevel, ...]


def validate_intraday_bar(bar: IntradayBar) -> IntradayBar:
    if bar.bar_end_at <= bar.bar_start_at:
        raise ValueError("intraday bar must have a positive interval")
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(not price.is_finite() or price <= 0 for price in prices):
        raise ValueError("intraday bar prices must be positive finite values")
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        raise ValueError("invalid intraday OHLC relationship")
    if bar.volume < 0 or not bar.amount.is_finite() or bar.amount < 0:
        raise ValueError("invalid intraday volume or amount")
    return bar


def validate_auction_levels(levels: Sequence[AuctionQuoteLevel]) -> Tuple[AuctionQuoteLevel, ...]:
    values = tuple(levels)
    expected = {(side, number) for side in ("BUY", "SELL") for number in range(1, 6)}
    actual = {(item.side, item.level_number) for item in values}
    if actual != expected:
        raise ValueError("auction quote requires exactly BUY/SELL levels 1 through 5")
    for item in values:
        if item.quantity < 0 or item.side not in {"BUY", "SELL"} or not 1 <= item.level_number <= 5:
            raise ValueError("invalid auction quote level")
        if item.price is not None and (not item.price.is_finite() or item.price <= 0):
            raise ValueError("invalid auction quote price")
    return values


class ExecutionMarketDataStore:
    """Persists immutable execution data and exposes only data visible by a cutoff."""

    def __init__(self, connection):
        self.connection = connection

    def store_intraday_bars(self, snapshot_id: str, bars: Iterable[IntradayBar]) -> None:
        rows = [
            (snapshot_id, item.ticker, item.bar_start_at, item.bar_end_at, item.open, item.high,
             item.low, item.close, item.volume, item.amount, item.status)
            for item in (validate_intraday_bar(bar) for bar in bars)
        ]
        if not rows:
            raise ValueError("intraday snapshot cannot be empty")
        self.connection.executemany("INSERT INTO intraday_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def load_intraday_bars_available_at(self, snapshot_id: str, ticker: str,
                                        decision_at: datetime) -> Tuple[IntradayBar, ...]:
        rows = self.connection.execute(
            "SELECT ticker, bar_start_at, bar_end_at, open, high, low, close, volume, amount, status "
            "FROM intraday_bars JOIN intraday_market_snapshots "
            "ON intraday_market_snapshots.intraday_snapshot_id = intraday_bars.intraday_snapshot_id "
            "WHERE intraday_bars.intraday_snapshot_id = ? AND ticker = ? AND bar_end_at <= ? "
            "AND intraday_market_snapshots.source_published_at <= ? "
            "AND intraday_market_snapshots.received_at <= ? "
            "ORDER BY bar_start_at",
            [snapshot_id, ticker, decision_at, decision_at, decision_at],
        ).fetchall()
        return tuple(IntradayBar(*row) for row in rows)

    def store_auction_quote(self, quote: AuctionQuote) -> None:
        levels = validate_auction_levels(quote.levels)
        self.connection.execute(
            "INSERT INTO auction_quote_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [quote.auction_snapshot_id, quote.trade_date, quote.ticker, quote.captured_at,
             quote.source_channel, quote.source_published_at, quote.received_at,
             quote.raw_artifact_path, quote.raw_artifact_sha256, quote.received_at],
        )
        self.connection.executemany(
            "INSERT INTO auction_quote_levels VALUES (?, ?, ?, ?, ?)",
            [(quote.auction_snapshot_id, item.side, item.level_number, item.price, item.quantity)
             for item in levels],
        )

    def latest_auction_quote_available_at(self, trade_date, ticker: str,
                                          decision_at: datetime) -> Optional[AuctionQuote]:
        row = self.connection.execute(
            "SELECT auction_snapshot_id, trade_date, ticker, captured_at, source_channel, source_published_at, "
            "received_at, raw_artifact_path, raw_artifact_sha256 "
            "FROM auction_quote_snapshots WHERE trade_date = ? AND ticker = ? AND captured_at <= ? "
            "AND received_at <= ? ORDER BY captured_at DESC, received_at DESC LIMIT 1",
            [trade_date, ticker, decision_at, decision_at],
        ).fetchone()
        if row is None:
            return None
        levels = self.connection.execute(
            "SELECT side, level_number, price, quantity FROM auction_quote_levels "
            "WHERE auction_snapshot_id = ? ORDER BY side, level_number", [row[0]],
        ).fetchall()
        return AuctionQuote(*row, tuple(AuctionQuoteLevel(*level) for level in levels))
