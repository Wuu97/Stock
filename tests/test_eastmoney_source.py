from datetime import date
from decimal import Decimal

from quant_core.eastmoney_source import parse_history_bars, parse_spot_quote, quote_to_bar, to_secid


def test_eastmoney_quote_normalizes_scaled_prices_and_market_cap():
    quote = parse_spot_quote("600000.SH", {
        "f43": 1050, "f44": 1060, "f45": 1030, "f46": 1040, "f47": 1000, "f48": 1050000, "f60": 1000,
        "f51": 1150, "f52": 950, "f116": 100000000000,
    })
    bar = quote_to_bar(quote, date(2026, 9, 8))
    assert to_secid("600000.SH") == "1.600000"
    assert to_secid("000001.SZ") == "0.000001"
    assert to_secid("300750.SZ") == "0.300750"
    assert to_secid("512400.SH") == "1.512400"
    assert to_secid("159740.SZ") == "0.159740"
    assert (bar.open, bar.close, bar.limit_up, bar.limit_down) == (
        Decimal("10.4"), Decimal("10.5"), Decimal("11.5"), Decimal("9.5")
    )
    assert quote.total_market_cap == Decimal("100000000000")
    assert quote.pre_close == Decimal("10")


def test_eastmoney_history_bars_are_unadjusted_and_do_not_invent_limits():
    bars = parse_history_bars("512400.SH", {"klines": ["2026-09-10,1.700,1.710,1.730,1.690,1200,2050.5,0,0,0,0"]})
    assert bars[0].trade_date == date(2026, 9, 10)
    assert (bars[0].open, bars[0].close, bars[0].volume, bars[0].amount) == (
        Decimal("1.700"), Decimal("1.710"), 1200, Decimal("2050.5")
    )
    assert bars[0].limit_up is None and bars[0].limit_down is None
