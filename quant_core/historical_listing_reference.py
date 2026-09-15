"""Build auditable stable listing-date references for historical eligibility reconstruction."""

from datetime import date
from typing import Iterable, Mapping


def baostock_lifecycle_row(ticker: str, row: Mapping[str, object]) -> dict:
    """Normalize BaoStock stock-basic output; unknown lifecycle facts never become eligible."""
    ipo, out = str(row.get("ipoDate") or ""), str(row.get("outDate") or "")
    if len(ipo) != 10 or not ticker:
        raise ValueError("BaoStock lifecycle response is missing an IPO date")
    return {"ts_code": ticker, "list_date": ipo.replace("-", ""),
            "delist_date": out.replace("-", "") or None, "list_status": "D" if out else "L"}


def unresolved_tickers(required: Iterable[str], known: Iterable[str]) -> list[str]:
    return sorted(set(required) - set(known))
