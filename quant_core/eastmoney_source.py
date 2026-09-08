"""Low-frequency Eastmoney spot adapter for a small A-share monitoring universe."""

from dataclasses import dataclass
from decimal import Decimal
import json
from typing import Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import DayBar


ENDPOINT = "https://push2.eastmoney.com/api/qt/stock/get"
FIELDS = "f43,f44,f45,f46,f47,f48,f51,f52,f57,f60,f116"


@dataclass(frozen=True)
class SpotQuote:
    ticker: str
    pre_close: Decimal
    last_price: Decimal
    high: Decimal
    low: Decimal
    open: Decimal
    volume: int
    amount: Decimal
    limit_up: Decimal
    limit_down: Decimal
    total_market_cap: Decimal


def fetch_spot_payload(ticker: str) -> Mapping[str, object]:
    """Fetch the vendor response unchanged for later snapshot evidence."""
    query = urlencode({"secid": to_secid(ticker), "fields": FIELDS, "ut": "fa5fd1943c7b386f172d6893dbbd1"})
    request = Request(f"{ENDPOINT}?{query}", headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Eastmoney returned no quote for {ticker}")
    return data


def fetch_spot_quote(ticker: str) -> SpotQuote:
    """Fetch and normalize one current quote for a small monitored universe."""
    return parse_spot_quote(ticker, fetch_spot_payload(ticker))


def parse_spot_quote(ticker: str, data: Mapping[str, object]) -> SpotQuote:
    """Normalize Eastmoney's cent-scaled price fields into Decimal CNY values."""
    return SpotQuote(
        ticker=ticker,
        pre_close=_price(data, "f60"), last_price=_price(data, "f43"),
        high=_price(data, "f44"), low=_price(data, "f45"), open=_price(data, "f46"),
        volume=int(_number(data, "f47")), amount=_number(data, "f48"),
        limit_up=_price(data, "f51"), limit_down=_price(data, "f52"), total_market_cap=_number(data, "f116"),
    )


def quote_to_bar(quote: SpotQuote, trade_date) -> DayBar:
    return DayBar(
        trade_date, quote.ticker, quote.open, quote.high, quote.low, quote.last_price,
        quote.volume, quote.amount, quote.limit_up, quote.limit_down,
        "TRADING" if quote.volume > 0 and quote.amount > 0 else "SUSPENDED",
    )


def to_secid(ticker: str) -> str:
    code, exchange = ticker.split(".", 1)
    if exchange == "SH" and code.startswith("6"):
        return f"1.{code}"
    if exchange == "SZ" and code.startswith(("00", "30")):
        return f"0.{code}"
    raise ValueError(f"unsupported A-share ticker for Eastmoney spot quote: {ticker}")


def _price(data: Mapping[str, object], field: str) -> Decimal:
    return _number(data, field) / Decimal("100")


def _number(data: Mapping[str, object], field: str) -> Decimal:
    value = data.get(field)
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise RuntimeError(f"Eastmoney quote has invalid {field}: {value}") from error
    if not parsed.is_finite() or parsed < 0:
        raise RuntimeError(f"Eastmoney quote has invalid {field}: {value}")
    return parsed
