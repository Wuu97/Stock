from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from scripts.daily_pipeline import _taxonomy_version, _tracked_tickers


def test_pipeline_tracks_pending_and_held_symbols_and_uses_latest_taxonomy():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('SW2021_V1', '1', '行业', 3)")
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('SW2021_V2', '2', '行业', 3)")
    connection.execute("INSERT INTO sim_accounts VALUES ('acct', '账户', 'CNY', 10000, 'cash', 'fifo', 'ACTIVE', ?)", [now])
    connection.execute(
        "INSERT INTO sim_order_intents VALUES ('pending', NULL, 'acct', '000001.SZ', ?, 'SELL', 100, 'NEXT_OPEN_WITH_SLIPPAGE', 'PENDING', NULL, ?)",
        [date(2026, 9, 10), now],
    )
    connection.execute(
        "INSERT INTO sim_position_lots VALUES ('lot', 'acct', '600000.SH', 'execution', ?, ?, 100, 10, ?)",
        [date(2026, 9, 1), date(2026, 9, 2), now],
    )
    assert _tracked_tickers(connection, "acct") == ["000001.SZ", "600000.SH"]
    assert _taxonomy_version(connection, None) == "SW2021_V2"
    assert _taxonomy_version(connection, "PINNED") == "PINNED"
