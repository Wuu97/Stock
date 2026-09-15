from quant_core.historical_listing_reference import baostock_lifecycle_row, unresolved_tickers
from scripts.build_historical_listing_reference import _required_tickers
from pathlib import Path
import duckdb


def test_baostock_lifecycle_reference_preserves_listing_and_delisting_dates():
    assert baostock_lifecycle_row("300114.SZ", {"ipoDate": "2010-08-27", "outDate": "2025-02-17"}) == {
        "ts_code": "300114.SZ", "list_date": "20100827", "delist_date": "20250217", "list_status": "D"
    }
    assert unresolved_tickers(["A", "B"], ["A"]) == ["B"]


def test_listing_reference_ticker_scope_is_bound_to_requested_dates():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    connection.execute("INSERT INTO market_cap_snapshots VALUES ('cap1', now(), 'fixture', now()), ('cap2', now(), 'fixture', now())")
    connection.execute("INSERT INTO universe_snapshots (universe_snapshot_id,group_name,as_of_trade_date,market_cap_snapshot_id,rule_version,rule_json,created_at) VALUES ('u1', 'g', '2023-01-01', 'cap1', 'v', '{}', now())")
    connection.execute("INSERT INTO universe_snapshots (universe_snapshot_id,group_name,as_of_trade_date,market_cap_snapshot_id,rule_version,rule_json,created_at) VALUES ('u2', 'g', '2024-01-01', 'cap2', 'v', '{}', now())")
    connection.execute("INSERT INTO market_cap_values VALUES ('cap1', 'OLD.SH', 90000000000), ('cap2', 'NEW.SH', 90000000000)")
    assert _required_tickers(connection, "g", "2024-01-01", "2024-12-31") == ["NEW.SH"]
