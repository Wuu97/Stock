"""BaoStock adapter for offline, unadjusted A-share daily bars.

BaoStock does not supply daily price-limit fields in this query. Those fields remain
empty and make the bar ineligible for the later daily matching engine by design.
"""

from datetime import date
from decimal import Decimal
from typing import Iterable, Iterator, Mapping

from .models import DayBar


FIELDS = "date,code,open,high,low,close,volume,amount,tradestatus"


def normalize_ticker(code: str) -> str:
    exchange, symbol = code.split(".", 1)
    return f"{symbol}.{exchange.upper()}"


def row_to_bar(row: Mapping[str, str]) -> DayBar:
    return DayBar(
        trade_date=date.fromisoformat(row["date"]), ticker=normalize_ticker(row["code"]),
        open=Decimal(row["open"]), high=Decimal(row["high"]), low=Decimal(row["low"]),
        close=Decimal(row["close"]), volume=int(Decimal(row["volume"])), amount=Decimal(row["amount"]),
        limit_up=None, limit_down=None, status="TRADING" if row["tradestatus"] == "1" else "SUSPENDED",
    )


def has_complete_quote(row: Mapping[str, str]) -> bool:
    """BaoStock may emit blank OHLCV fields for suspended or malformed rows."""
    return all(row.get(field, "").strip() for field in ("open", "high", "low", "close", "volume", "amount"))


def download_daily(codes: Iterable[str], start_date: date, end_date: date) -> Iterator[DayBar]:
    """Download unadjusted daily bars; the optional baostock extra is required."""
    try:
        import baostock as bs
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        for code in codes:
            response = bs.query_history_k_data_plus(
                code, FIELDS, start_date=start_date.isoformat(), end_date=end_date.isoformat(),
                frequency="d", adjustflag="3",
            )
            if response.error_code != "0":
                raise RuntimeError(f"BaoStock query failed for {code}: {response.error_msg}")
            while response.next():
                row = dict(zip(response.fields, response.get_row_data()))
                if has_complete_quote(row):
                    yield row_to_bar(row)
    finally:
        bs.logout()
