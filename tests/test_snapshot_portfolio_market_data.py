from datetime import datetime, timezone
from pathlib import Path

import duckdb

from scripts.snapshot_portfolio_market_data import _held_tickers


def test_held_tickers_excludes_frozen_research_accounts():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    connection.executemany("INSERT INTO sim_accounts VALUES (?, ?, 'CNY', 1000, 'cash', 'fifo', ?, ?)", [
        ("forward", "Forward", "ACTIVE", now), ("research", "Research", "FROZEN", now),
    ])
    connection.executemany("INSERT INTO sim_position_lots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        ("active-lot", "forward", "600000.SH", "event-a", now.date(), now.date(), 100, 10, now),
        ("frozen-lot", "research", "601238.SH", "event-r", now.date(), now.date(), 100, 10, now),
    ])
    assert _held_tickers(connection) == [("600000.SH", "A_SHARE")]
