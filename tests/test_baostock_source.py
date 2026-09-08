from decimal import Decimal

from quant_core.baostock_source import has_complete_quote, normalize_ticker, row_to_bar


def test_baostock_rows_are_normalized_without_inventing_price_limits():
    bar = row_to_bar({
        "date": "2026-09-01", "code": "sh.600000", "open": "10", "high": "11", "low": "9",
        "close": "10.5", "volume": "1000", "amount": "10500", "tradestatus": "1",
    })
    assert normalize_ticker("sz.000001") == "000001.SZ"
    assert bar.ticker == "600000.SH"
    assert bar.close == Decimal("10.5")
    assert bar.limit_up is None and bar.limit_down is None


def test_baostock_blank_quotes_are_not_treated_as_prices():
    assert not has_complete_quote({"open": "", "high": "", "low": "", "close": "", "volume": "", "amount": ""})
