from quant_core.historical_listing_reference import baostock_lifecycle_row, unresolved_tickers


def test_baostock_lifecycle_reference_preserves_listing_and_delisting_dates():
    assert baostock_lifecycle_row("300114.SZ", {"ipoDate": "2010-08-27", "outDate": "2025-02-17"}) == {
        "ts_code": "300114.SZ", "list_date": "20100827", "delist_date": "20250217", "list_status": "D"
    }
    assert unresolved_tickers(["A", "B"], ["A"]) == ["B"]
