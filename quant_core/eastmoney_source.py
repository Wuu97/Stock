"""Low-frequency Eastmoney spot adapter for listed A-shares and exchange-traded funds."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
import time
from typing import Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import DayBar


ENDPOINT = "https://push2.eastmoney.com/api/qt/stock/get"
HISTORY_ENDPOINT = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
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


def fetch_history_payload(ticker: str, start_date, end_date) -> Mapping[str, object]:
    """Fetch unadjusted daily K-line payload for one listed A-share or ETF."""
    query = urlencode({
        "secid": to_secid(ticker), "klt": "101", "fqt": "0",
        "beg": start_date.strftime("%Y%m%d"), "end": end_date.strftime("%Y%m%d"),
        "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "fa5fd1943c7b386f172d6893dbbd1",
    })
    request = Request(f"{HISTORY_ENDPOINT}?{query}", headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    payload = _read_json_with_retries(request, ticker)
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("klines"), list):
        raise RuntimeError(f"Eastmoney returned no daily history for {ticker}")
    return data


def _read_json_with_retries(request: Request, ticker: str) -> Mapping[str, object]:
    """Retry short-lived vendor disconnects without hiding a persistent failure."""
    last_error = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Eastmoney response is not an object")
            return payload
        except (OSError, ValueError, RuntimeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Eastmoney history request failed for {ticker} after retries") from last_error


def parse_history_bars(ticker: str, data: Mapping[str, object]):
    """Convert Eastmoney's unadjusted daily K-lines without inventing price limits."""
    bars = []
    for line in data.get("klines", []):
        values = str(line).split(",")
        if len(values) < 7:
            raise RuntimeError(f"Eastmoney K-line has too few fields for {ticker}: {line}")
        trade_date = datetime.strptime(values[0], "%Y-%m-%d").date()
        open_price, close, high, low = (Decimal(values[index]) for index in (1, 2, 3, 4))
        volume, amount = int(Decimal(values[5])), Decimal(values[6])
        status = "TRADING" if volume > 0 and amount > 0 else "SUSPENDED"
        bars.append(DayBar(trade_date, ticker, open_price, high, low, close, volume, amount, None, None, status))
    if not bars:
        raise RuntimeError(f"Eastmoney returned empty daily history for {ticker}")
    return tuple(bars)


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
    if exchange == "SH" and code.startswith(("5", "6", "9")):
        return f"1.{code}"
    if exchange == "SZ" and code.startswith(("0", "1", "2", "3")):
        return f"0.{code}"
    raise ValueError(f"unsupported SSE/SZSE ticker for Eastmoney spot quote: {ticker}")


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
