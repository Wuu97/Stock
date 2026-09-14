"""Tushare minute adapters for immutable intraday replay snapshots.

Tushare publishes historical minute data after the market close.  This adapter
is therefore for replay and research snapshots, never a substitute for a live
auction/order-book feed.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

from .execution_data import IntradayBar
from .tushare_source import create_tushare_client


SHANGHAI = ZoneInfo("Asia/Shanghai")


def fetch_stock_minute_records(token: str, ticker: str, frequency: str,
                               start_at: datetime, end_at: datetime) -> Tuple[Mapping[str, object], ...]:
    """Fetch one ticker only, matching Tushare's minute endpoint contract."""
    if frequency not in {"1min", "5min", "15min", "60min"}:
        raise ValueError("only 1min, 5min, 15min, and 60min are supported")
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("minute request timestamps must be timezone-aware")
    if start_at >= end_at:
        raise ValueError("minute request start must be before end")
    frame = create_tushare_client(token).stk_mins(
        ts_code=ticker, freq=frequency,
        start_date=_local_timestamp(start_at), end_date=_local_timestamp(end_at),
    )
    required = {"ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount"}
    if not required.issubset(frame.columns):
        raise RuntimeError("Tushare stk_mins response is missing required fields")
    return tuple(frame.to_dict("records"))


def fetch_realtime_minute_records(token: str, tickers: Sequence[str], frequency: str) -> Tuple[Mapping[str, object], ...]:
    """Fetch completed real-time bars, never auction order-book data.

    Tushare's ``rt_min`` endpoint is a polling input.  Callers must archive
    every response and treat its receipt time as the earliest usable time.
    """
    if frequency not in {"1min", "5min", "15min", "60min"}:
        raise ValueError("only 1min, 5min, 15min, and 60min are supported")
    normalized = tuple(sorted(set(tickers)))
    if not normalized:
        raise ValueError("at least one ticker is required")
    frame = create_tushare_client(token).rt_min(ts_code=",".join(normalized), freq=frequency.upper())
    required = {"ts_code", "time", "open", "close", "high", "low", "vol", "amount"}
    if not required.issubset(frame.columns):
        raise RuntimeError("Tushare rt_min response is missing required fields")
    return tuple(frame.to_dict("records"))


def minute_records_to_bars(ticker: str, frequency: str,
                           records: Sequence[Mapping[str, object]]) -> Tuple[IntradayBar, ...]:
    minutes = _frequency_minutes(frequency)
    bars = []
    for row in records:
        row_ticker = str(row["ts_code"])
        if row_ticker != ticker:
            raise ValueError(f"unexpected ticker {row_ticker} in {ticker} minute response")
        bars.append(_record_to_bar(ticker, minutes, row, "trade_time"))
    return tuple(sorted(bars, key=lambda bar: bar.bar_start_at))


def realtime_minute_records_to_bars(frequency: str,
                                    records: Sequence[Mapping[str, object]]) -> Tuple[IntradayBar, ...]:
    """Normalize a real-time response using its completed-bar ``time`` field."""
    minutes = _frequency_minutes(frequency)
    return tuple(sorted(
        (_record_to_bar(str(row["ts_code"]), minutes, row, "time") for row in records),
        key=lambda bar: (bar.ticker, bar.bar_start_at),
    ))


def _record_to_bar(ticker: str, minutes: int, row: Mapping[str, object], timestamp_field: str) -> IntradayBar:
    # Both Tushare endpoints timestamp completed bars. Treating the timestamp
    # as bar_end_at prevents an unfinished bar leaking into a replay decision.
    end_at = _parse_trade_time(str(row[timestamp_field]))
    return IntradayBar(
        ticker=ticker,
        bar_start_at=end_at - timedelta(minutes=minutes),
        bar_end_at=end_at,
        open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
        volume=int(Decimal(str(row["vol"])),), amount=Decimal(str(row["amount"])),
        status="TRADING",
    )


def _frequency_minutes(frequency: str) -> int:
    if frequency not in {"1min", "5min", "15min", "60min"}:
        raise ValueError("unsupported minute frequency")
    return int(frequency.removesuffix("min"))


def _local_timestamp(value: datetime) -> str:
    return value.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


def _parse_trade_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=SHANGHAI) if parsed.tzinfo is None else parsed.astimezone(SHANGHAI)
