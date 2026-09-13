"""BaoStock historical ST-flag adapter kept separate from official Tushare facts."""

from contextlib import contextmanager
from datetime import date
from typing import Iterator, Tuple


FIELDS = "date,code,isST"


def baostock_code(ticker: str) -> str:
    symbol, exchange = ticker.split(".", 1)
    if exchange not in {"SH", "SZ"}:
        raise ValueError("BaoStock ST history supports only SH/SZ tickers")
    return f"{exchange.lower()}.{symbol}"


@contextmanager
def session():
    try:
        import baostock as bs
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
    try:
        yield bs
    finally:
        bs.logout()


def fetch_is_st(client, ticker: str, start_date: date, end_date: date) -> Tuple[tuple[date, bool], ...]:
    response = client.query_history_k_data_plus(
        baostock_code(ticker), FIELDS, start_date=start_date.isoformat(), end_date=end_date.isoformat(),
        frequency="d", adjustflag="3",
    )
    if response.error_code != "0":
        raise RuntimeError(f"BaoStock query failed for {ticker}: {response.error_msg}")
    rows = []
    while response.next():
        values = dict(zip(response.fields, response.get_row_data()))
        flag = values.get("isST", "")
        if flag not in {"0", "1"}:
            raise RuntimeError(f"BaoStock returned invalid isST value for {ticker}")
        rows.append((date.fromisoformat(values["date"]), flag == "1"))
    return tuple(rows)
