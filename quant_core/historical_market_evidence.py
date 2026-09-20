"""Validation and canonicalization for immutable historical daily-market evidence."""

from datetime import date, datetime
from decimal import Decimal
import json
from typing import Iterable, Mapping

from .models import DayBar
from .tushare_source import merge_daily_limits, row_to_daily_limit


DAILY_FIELDS = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}
BASIC_FIELDS = {"ts_code", "trade_date", "total_mv"}
FACTOR_FIELDS = {"ts_code", "trade_date", "adj_factor"}
LIMIT_FIELDS = {"ts_code", "trade_date", "up_limit", "down_limit"}


def is_mainland_a_share(ticker: str) -> bool:
    """Keep SSE/SZSE A shares; BSE and B shares are outside this contract."""
    return ticker.endswith((".SH", ".SZ")) and not ticker.startswith(("200", "900"))


def require_records(frame, fields: set[str], endpoint: str) -> list[dict]:
    if not fields.issubset(frame.columns):
        raise ValueError(f"Tushare {endpoint} response is missing required fields")
    records = frame.to_dict("records")
    if not records:
        raise ValueError(f"Tushare {endpoint} response is empty for a trading day")
    return records


def _require_unique_date_ticker(records: Iterable[Mapping], endpoint: str, expected_day: date) -> None:
    keys = [(str(row["trade_date"]), str(row["ts_code"])) for row in records]
    if any(datetime.strptime(key[0], "%Y%m%d").date() != expected_day for key in keys):
        raise ValueError(f"Tushare {endpoint} returned an unexpected trade date")
    if len(keys) != len(set(keys)):
        raise ValueError(f"Tushare {endpoint} contains duplicate date/ticker rows")


def _positive_decimal(row: Mapping, field: str, endpoint: str) -> Decimal:
    """Reject null, non-numeric, zero, and negative execution inputs."""
    try:
        value = Decimal(str(row[field]))
    except Exception as error:
        raise ValueError(f"Tushare {endpoint} has a non-numeric {field}") from error
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Tushare {endpoint} has an invalid {field}")
    return value


def canonical_daily_evidence(trade_date: date, daily_all: list[dict], basic_all: list[dict],
                             factors_all: list[dict], limits_all: list[dict],
                             minimum_execution_market_cap: Decimal = Decimal("80000000000"),
                             prior_execution_tickers: Iterable[str] = ()) -> tuple[list[DayBar], dict[str, Decimal], dict]:
    """Fail closed on incomplete per-day provider joins and build normalized facts.

    The raw endpoint payloads remain in the returned evidence bundle.  Normalized
    bars/caps are derived only after every daily ticker can be joined to its
    market-cap, adjustment-factor, and price-limit evidence.
    """
    for endpoint, records in (("daily", daily_all), ("daily_basic", basic_all),
                              ("adj_factor", factors_all), ("stk_limit", limits_all)):
        _require_unique_date_ticker(records, endpoint, trade_date)
    daily = [row for row in daily_all if is_mainland_a_share(str(row["ts_code"]))]
    if not daily:
        raise ValueError(f"Tushare daily has no in-scope mainland A-share rows for {trade_date}")
    tickers = {str(row["ts_code"]) for row in daily}
    basic_by_ticker = {str(row["ts_code"]): Decimal(str(row["total_mv"])) * Decimal("10000")
                       for row in basic_all if str(row["ts_code"]) in tickers}
    execution_limit_tickers = ({ticker for ticker, cap in basic_by_ticker.items()
                                if cap >= minimum_execution_market_cap}
                               | (set(prior_execution_tickers) & tickers))
    for endpoint, records in (("daily_basic", basic_all), ("adj_factor", factors_all), ("stk_limit", limits_all)):
        available = {str(row["ts_code"]) for row in records if is_mainland_a_share(str(row["ts_code"]))}
        missing = sorted((execution_limit_tickers if endpoint == "stk_limit" else tickers) - available)
        if missing:
            raise ValueError(f"Tushare {endpoint} is incomplete for {len(missing)} daily tickers on {trade_date}: {missing[:10]}")
    for row in daily:
        for field in ("open", "high", "low", "close", "vol", "amount"):
            _positive_decimal(row, field, "daily")
        if _positive_decimal(row, "high", "daily") < _positive_decimal(row, "low", "daily"):
            raise ValueError("Tushare daily has high below low")
    for row in basic_all:
        if str(row["ts_code"]) in tickers:
            _positive_decimal(row, "total_mv", "daily_basic")
    for row in factors_all:
        if str(row["ts_code"]) in tickers:
            _positive_decimal(row, "adj_factor", "adj_factor")
    for row in limits_all:
        if str(row["ts_code"]) in tickers:
            up, down = _positive_decimal(row, "up_limit", "stk_limit"), _positive_decimal(row, "down_limit", "stk_limit")
            if up < down:
                raise ValueError("Tushare stk_limit has up_limit below down_limit")
    raw_bars = [DayBar(
        trade_date, str(row["ts_code"]), Decimal(str(row["open"])), Decimal(str(row["high"])),
        Decimal(str(row["low"])), Decimal(str(row["close"])), int(Decimal(str(row["vol"])) * Decimal("100")),
        Decimal(str(row["amount"])) * Decimal("1000"), None, None,
    ) for row in daily]
    limits = [row_to_daily_limit(row) for row in limits_all if str(row["ts_code"]) in tickers]
    bars = list(merge_daily_limits(raw_bars, limits))
    caps = {str(row["ts_code"]): Decimal(str(row["total_mv"])) * Decimal("10000")
            for row in basic_all if str(row["ts_code"]) in tickers}
    if set(caps) != tickers:
        raise ValueError("daily_basic join did not preserve the daily ticker domain")
    evidence = {
        "evidence_version": "historical_market_evidence_v1",
        "request_identity": {"provider": "tushare", "trade_date": trade_date.isoformat(),
                             "endpoints": ["daily", "daily_basic", "adj_factor", "stk_limit"]},
        "coverage_contract": {"price_limits_required_for": "current_or_prior_execution_candidate_domain",
                              "minimum_execution_market_cap": str(minimum_execution_market_cap),
                              "prior_execution_ticker_count": len(set(prior_execution_tickers)),
                              "adjustment_factor_required_for": "all_mainland_a_share_daily_bars",
                              "adjustment_factor_contract": "raw_ohlcv_and_adj_factor_are_frozen_together; execution uses raw trade-date prices"},
        "responses": {"daily": daily_all, "daily_basic": basic_all, "adj_factor": factors_all, "stk_limit": limits_all},
    }
    return bars, caps, evidence


def canonical_evidence_bytes(evidence: Mapping) -> bytes:
    return (json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode("utf-8")
