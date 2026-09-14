from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.execution_data import (
    AuctionQuote,
    AuctionQuoteLevel,
    ExecutionMarketDataStore,
    IntradayBar,
)


UTC = timezone.utc


def _connection():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    return connection


def _levels():
    return tuple(
        AuctionQuoteLevel(side, number, Decimal("10.00") if number == 1 else None, number * 100)
        for side in ("BUY", "SELL") for number in range(1, 6)
    )


def test_intraday_store_only_exposes_completed_bars_at_decision_time():
    connection = _connection()
    start = datetime(2026, 9, 14, 9, 30, tzinfo=UTC)
    connection.execute(
        "INSERT INTO intraday_market_snapshots VALUES ('m5', '2026-09-14', 5, 'test', ?, ?, 'manifest', 'hash', ?)",
        [start, start, start],
    )
    store = ExecutionMarketDataStore(connection)
    store.store_intraday_bars("m5", (
        IntradayBar("600000.SH", start, start + timedelta(minutes=5), Decimal("10"), Decimal("10.2"), Decimal("9.9"), Decimal("10.1"), 100, Decimal("1000")),
        IntradayBar("600000.SH", start + timedelta(minutes=5), start + timedelta(minutes=10), Decimal("10.1"), Decimal("10.3"), Decimal("10"), Decimal("10.2"), 100, Decimal("1000")),
    ))
    assert store.load_intraday_bars_available_at("m5", "600000.SH", start + timedelta(minutes=7)) == (
        IntradayBar("600000.SH", start, start + timedelta(minutes=5), Decimal("10"), Decimal("10.2"), Decimal("9.9"), Decimal("10.1"), 100, Decimal("1000")),
    )


def test_intraday_snapshot_is_not_visible_before_it_was_received():
    connection = _connection()
    start = datetime(2026, 9, 14, 9, 30, tzinfo=UTC)
    received = start + timedelta(minutes=10)
    connection.execute(
        "INSERT INTO intraday_market_snapshots VALUES ('late', '2026-09-14', 5, 'test', ?, ?, 'manifest', 'hash', ?)",
        [received, received, received],
    )
    store = ExecutionMarketDataStore(connection)
    store.store_intraday_bars("late", (
        IntradayBar("600000.SH", start, start + timedelta(minutes=5), Decimal("10"), Decimal("10.2"), Decimal("9.9"), Decimal("10.1"), 100, Decimal("1000")),
    ))
    assert store.load_intraday_bars_available_at("late", "600000.SH", start + timedelta(minutes=7)) == ()


def test_auction_quote_is_not_visible_before_capture_or_receipt_time():
    connection = _connection()
    store = ExecutionMarketDataStore(connection)
    captured = datetime(2026, 9, 14, 9, 24, 30, tzinfo=UTC)
    quote = AuctionQuote("a1", date(2026, 9, 14), "600000.SH", captured, "test", captured,
                         captured + timedelta(seconds=5), "raw.json", "hash", _levels())
    store.store_auction_quote(quote)
    assert store.latest_auction_quote_available_at(date(2026, 9, 14), "600000.SH", captured) is None
    observed = store.latest_auction_quote_available_at(date(2026, 9, 14), "600000.SH", captured + timedelta(seconds=5))
    assert observed is not None
    assert observed.auction_snapshot_id == "a1"
    assert observed.levels[0] == AuctionQuoteLevel("BUY", 1, Decimal("10.00"), 100)


def test_auction_quote_rejects_incomplete_five_level_book():
    connection = _connection()
    store = ExecutionMarketDataStore(connection)
    now = datetime(2026, 9, 14, 9, 24, tzinfo=UTC)
    with pytest.raises(ValueError, match="exactly"):
        store.store_auction_quote(AuctionQuote("bad", date(2026, 9, 14), "600000.SH", now, "test", now, now,
                                               "raw", "hash", _levels()[:-1]))
