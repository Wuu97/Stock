"""Tushare ``stk_limit`` adapter for authoritative historical A-share price limits."""

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping, Sequence, Tuple

from .models import DayBar


@dataclass(frozen=True)
class DailyLimit:
    trade_date: date
    ticker: str
    up_limit: Decimal
    down_limit: Decimal

    def __post_init__(self) -> None:
        if not self.up_limit.is_finite() or not self.down_limit.is_finite() or self.down_limit <= 0:
            raise ValueError("Tushare limit prices must be finite and positive")
        if self.up_limit < self.down_limit:
            raise ValueError("Tushare upper limit cannot be below lower limit")


def row_to_daily_limit(row: Mapping[str, object]) -> DailyLimit:
    return DailyLimit(
        trade_date=datetime.strptime(str(row["trade_date"]), "%Y%m%d").date(),
        ticker=str(row["ts_code"]),
        up_limit=Decimal(str(row["up_limit"])),
        down_limit=Decimal(str(row["down_limit"])),
    )


def fetch_daily_limit_records(token: str, trade_dates: Sequence[date]) -> Tuple[Mapping[str, object], ...]:
    """Fetch raw Tushare rows before converting them into domain values."""
    if not token:
        raise ValueError("Tushare token cannot be empty")
    try:
        import tushare as ts
    except ImportError as error:
        raise RuntimeError("install the data extra: pip install '.[data]'") from error
    client = ts.pro_api(token)
    records = []
    for trade_date in sorted(set(trade_dates)):
        frame = client.stk_limit(trade_date=trade_date.strftime("%Y%m%d"))
        required = {"trade_date", "ts_code", "up_limit", "down_limit"}
        if not required.issubset(frame.columns):
            raise RuntimeError("Tushare stk_limit response is missing required fields")
        records.extend(frame.to_dict("records"))
    return tuple(records)


def fetch_daily_limits(token: str, trade_dates: Sequence[date]) -> Tuple[DailyLimit, ...]:
    """Fetch one full-market Tushare limit-price snapshot per requested date."""
    return tuple(row_to_daily_limit(row) for row in fetch_daily_limit_records(token, trade_dates))


def merge_daily_limits(bars: Iterable[DayBar], limits: Iterable[DailyLimit]) -> Tuple[DayBar, ...]:
    """Return copied bars with vendor-supplied price limits joined by date and ticker."""
    by_key = {(limit.trade_date, limit.ticker): limit for limit in limits}
    return tuple(
        replace(bar, limit_up=limit.up_limit, limit_down=limit.down_limit)
        if (limit := by_key.get((bar.trade_date, bar.ticker))) else bar
        for bar in bars
    )


def missing_limits(bars: Iterable[DayBar], required_tickers: Iterable[str]) -> Tuple[Tuple[date, str], ...]:
    required = set(required_tickers)
    return tuple(
        (bar.trade_date, bar.ticker)
        for bar in bars
        if bar.ticker in required and (bar.limit_up is None or bar.limit_down is None)
    )
