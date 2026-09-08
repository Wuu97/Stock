from datetime import date
from decimal import Decimal

from quant_core.eastmoney_source import parse_spot_quote, quote_to_bar, to_secid


def test_eastmoney_quote_normalizes_scaled_prices_and_market_cap():
    quote = parse_spot_quote("600000.SH", {
        "f43": 1050, "f44": 1060, "f45": 1030, "f46": 1040, "f47": 1000, "f48": 1050000, "f60": 1000,
        "f51": 1150, "f52": 950, "f116": 100000000000,
    })
    bar = quote_to_bar(quote, date(2026, 9, 8))
    assert to_secid("600000.SH") == "1.600000"
    assert to_secid("000001.SZ") == "0.000001"
    assert to_secid("300750.SZ") == "0.300750"
    assert (bar.open, bar.close, bar.limit_up, bar.limit_down) == (
        Decimal("10.4"), Decimal("10.5"), Decimal("11.5"), Decimal("9.5")
    )
    assert quote.total_market_cap == Decimal("100000000000")
    assert quote.pre_close == Decimal("10")
