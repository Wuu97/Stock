"""Point-in-time dynamic monitoring universes based on current market cap and past returns."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Union
from uuid import uuid4

from .models import DayBar


@dataclass(frozen=True)
class DynamicUniverseRule:
    group_name: str
    min_total_market_cap: Decimal
    momentum_window_days: int
    top_n: int
    rule_version: str = "current_cap_momentum_v1"
    min_listing_trading_days: int = 0

    def __post_init__(self) -> None:
        if not self.group_name.strip():
            raise ValueError("group_name cannot be blank")
        if (self.min_total_market_cap < 0 or self.momentum_window_days <= 0 or self.top_n <= 0
                or self.min_listing_trading_days < 0):
            raise ValueError("dynamic universe rule values must be non-negative and non-zero")

    def as_json(self) -> str:
        return json.dumps({
            "minimum_total_market_cap": str(self.min_total_market_cap),
            "momentum_window_days": self.momentum_window_days,
            "ranking_metric": "close_return",
            "top_n": self.top_n,
            "min_listing_trading_days": self.min_listing_trading_days,
        }, sort_keys=True)


@dataclass(frozen=True)
class LiquidityUniverseRule:
    """PIT candidate pool bounded by market-cap range and daily tradability."""

    group_name: str
    min_total_market_cap: Decimal
    max_total_market_cap: Optional[Decimal]
    liquidity_lookback_days: int
    min_average_amount: Decimal
    momentum_window_days: int
    min_momentum: Optional[Decimal]
    top_n: int
    rule_version: str = "liquidity_momentum_candidate_v1"
    min_listing_trading_days: int = 0

    def __post_init__(self) -> None:
        if (not self.group_name.strip() or self.min_total_market_cap < 0 or self.max_total_market_cap is not None
                and self.max_total_market_cap < self.min_total_market_cap or self.liquidity_lookback_days < 2
                or self.min_average_amount < 0 or self.momentum_window_days < 1 or self.top_n < 1
                or self.min_listing_trading_days < 0):
            raise ValueError("liquidity universe rule values are invalid")

    def as_json(self) -> str:
        return json.dumps({
            "minimum_total_market_cap": str(self.min_total_market_cap),
            "maximum_total_market_cap": None if self.max_total_market_cap is None else str(self.max_total_market_cap),
            "liquidity_lookback_days": self.liquidity_lookback_days,
            "minimum_average_amount": str(self.min_average_amount),
            "momentum_window_days": self.momentum_window_days,
            "minimum_momentum": None if self.min_momentum is None else str(self.min_momentum),
            "ranking_metric": "close_return_with_liquidity_filter",
            "top_n": self.top_n,
            "min_listing_trading_days": self.min_listing_trading_days,
        }, sort_keys=True)


@dataclass(frozen=True)
class FixedUniverseRule:
    """A deliberately stable monitoring group whose members are explicitly named."""
    group_name: str
    tickers: Sequence[str]
    rule_version: str = "fixed_tickers_v1"

    def __post_init__(self) -> None:
        if not self.group_name.strip():
            raise ValueError("group_name cannot be blank")
        normalized = tuple(sorted(set(self.tickers)))
        if not normalized or any(not ticker.strip() for ticker in normalized):
            raise ValueError("fixed universe requires non-blank tickers")
        object.__setattr__(self, "tickers", normalized)

    def as_json(self) -> str:
        return json.dumps({"membership": "fixed", "tickers": list(self.tickers)}, sort_keys=True)


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    total_market_cap: Decimal
    momentum: Decimal
    rank: int


def _listing_age_is_eligible(ticker: str, as_of_trade_date: date, min_days: int,
                             listing_dates: Optional[Mapping[str, date]], trading_days: Optional[Sequence[date]]) -> bool:
    if min_days == 0:
        return True
    if listing_dates is None or trading_days is None or ticker not in listing_dates:
        return False
    return sum(listing_dates[ticker] <= day <= as_of_trade_date for day in trading_days) >= min_days


def select_dynamic_universe(bars: Iterable[DayBar], market_caps: Mapping[str, Decimal],
                            as_of_trade_date: date, rule: DynamicUniverseRule,
                            listing_dates: Optional[Mapping[str, date]] = None,
                            trading_days: Optional[Sequence[date]] = None) -> List[UniverseMember]:
    """Use current market cap only for today's pool; returns use bars available by as-of date."""
    by_ticker: Dict[str, List[DayBar]] = {}
    for bar in bars:
        if bar.trade_date <= as_of_trade_date and bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    candidates = []
    for ticker, market_cap in market_caps.items():
        history = sorted(by_ticker.get(ticker, []), key=lambda bar: bar.trade_date)
        if (market_cap < rule.min_total_market_cap or len(history) <= rule.momentum_window_days
                or not _listing_age_is_eligible(ticker, as_of_trade_date, rule.min_listing_trading_days,
                                                 listing_dates, trading_days)):
            continue
        current, prior = history[-1].close, history[-rule.momentum_window_days - 1].close
        candidates.append((ticker, market_cap, (current / prior) - Decimal("1")))
    candidates.sort(key=lambda item: (-item[2], item[0]))
    return [UniverseMember(ticker, cap, momentum, rank) for rank, (ticker, cap, momentum)
            in enumerate(candidates[:rule.top_n], start=1)]


def select_liquidity_universe(bars: Iterable[DayBar], market_caps: Mapping[str, Decimal],
                              as_of_trade_date: date, rule: LiquidityUniverseRule,
                              listing_dates: Optional[Mapping[str, date]] = None,
                              trading_days: Optional[Sequence[date]] = None) -> List[UniverseMember]:
    """Select only from facts available at the decision close.

    This is intentionally a candidate pool. It does not infer ST status, listing
    age, or news validity when the corresponding PIT data has not been supplied.
    """
    by_ticker: Dict[str, List[DayBar]] = {}
    required_history = max(rule.liquidity_lookback_days, rule.momentum_window_days + 1)
    for bar in bars:
        if bar.trade_date <= as_of_trade_date and bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    candidates = []
    for ticker, market_cap in market_caps.items():
        if (market_cap < rule.min_total_market_cap
                or rule.max_total_market_cap is not None and market_cap > rule.max_total_market_cap
                or not _listing_age_is_eligible(ticker, as_of_trade_date, rule.min_listing_trading_days,
                                                 listing_dates, trading_days)):
            continue
        history = sorted(by_ticker.get(ticker, []), key=lambda bar: bar.trade_date)
        if len(history) < required_history:
            continue
        liquidity_window = history[-rule.liquidity_lookback_days:]
        average_amount = sum((bar.amount for bar in liquidity_window), Decimal("0")) / rule.liquidity_lookback_days
        if average_amount < rule.min_average_amount:
            continue
        momentum = (history[-1].close / history[-rule.momentum_window_days - 1].close) - Decimal("1")
        if rule.min_momentum is not None and momentum < rule.min_momentum:
            continue
        candidates.append((ticker, market_cap, momentum))
    candidates.sort(key=lambda item: (-item[2], item[0]))
    return [UniverseMember(ticker, cap, momentum, rank) for rank, (ticker, cap, momentum)
            in enumerate(candidates[:rule.top_n], start=1)]


class UniverseService:
    def __init__(self, connection):
        self.connection = connection

    def store_market_caps(self, snapshot_id: str, captured_at: datetime, source_channel: str,
                          values: Mapping[str, Decimal], created_at: datetime) -> None:
        if not values:
            raise ValueError("market-cap snapshot cannot be empty")
        self.connection.execute("INSERT INTO market_cap_snapshots VALUES (?, ?, ?, ?)",
                                [snapshot_id, captured_at, source_channel, created_at])
        self.connection.executemany("INSERT INTO market_cap_values VALUES (?, ?, ?)",
                                    [(snapshot_id, ticker, cap) for ticker, cap in values.items()])

    def create_snapshot(self, market_cap_snapshot_id: str, as_of_trade_date: date,
                        rule: Union[DynamicUniverseRule, LiquidityUniverseRule], bars: Iterable[DayBar], created_at: datetime,
                        listing_snapshot_id: Optional[str] = None) -> str:
        values = self.connection.execute("SELECT ticker, total_market_cap FROM market_cap_values WHERE market_cap_snapshot_id = ?",
                                         [market_cap_snapshot_id]).fetchall()
        caps = {ticker: Decimal(str(cap)) for ticker, cap in values}
        listing_dates, trading_days = self._listing_inputs(as_of_trade_date, rule, listing_snapshot_id)
        members = (select_dynamic_universe(bars, caps, as_of_trade_date, rule, listing_dates, trading_days)
                   if isinstance(rule, DynamicUniverseRule)
                   else select_liquidity_universe(bars, caps, as_of_trade_date, rule, listing_dates, trading_days))
        snapshot_id = str(uuid4())
        self.connection.execute(
            "INSERT INTO universe_snapshots (universe_snapshot_id, group_name, as_of_trade_date, "
            "market_cap_snapshot_id, rule_version, rule_json, created_at, listing_snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [snapshot_id, rule.group_name, as_of_trade_date, market_cap_snapshot_id, rule.rule_version,
             rule.as_json(), created_at, listing_snapshot_id],
        )
        member_rows = [
            (snapshot_id, member.ticker, member.total_market_cap, member.momentum, member.rank)
            for member in members
        ]
        if member_rows:
            self.connection.executemany("INSERT INTO universe_members VALUES (?, ?, ?, ?, ?)", member_rows)
        return snapshot_id

    def _listing_inputs(self, as_of_trade_date: date, rule: Union[DynamicUniverseRule, LiquidityUniverseRule],
                        listing_snapshot_id: Optional[str]) -> tuple[Optional[Mapping[str, date]], Optional[Sequence[date]]]:
        if rule.min_listing_trading_days == 0:
            return None, None
        if not listing_snapshot_id:
            raise ValueError("listing_snapshot_id is required when a universe enforces listing age")
        rows = self.connection.execute(
            "SELECT ticker, list_date FROM security_listing_values WHERE listing_snapshot_id = ?", [listing_snapshot_id]
        ).fetchall()
        if not rows:
            raise ValueError("listing snapshot is missing or empty")
        trading_days = [row[0] for row in self.connection.execute(
            "SELECT DISTINCT trade_date FROM daily_bars WHERE trade_date <= ? ORDER BY trade_date", [as_of_trade_date]
        ).fetchall()]
        if len(trading_days) < rule.min_listing_trading_days:
            raise ValueError("local market calendar is insufficient for listing-age eligibility")
        return {ticker: list_date for ticker, list_date in rows}, trading_days

    def create_fixed_snapshot(self, market_cap_snapshot_id: str, as_of_trade_date: date,
                              rule: FixedUniverseRule, created_at: datetime) -> str:
        """Persist an explicit membership set while retaining its cap-snapshot lineage."""
        values = self.connection.execute(
            "SELECT ticker, total_market_cap FROM market_cap_values WHERE market_cap_snapshot_id = ?",
            [market_cap_snapshot_id],
        ).fetchall()
        caps = {ticker: Decimal(str(cap)) for ticker, cap in values}
        security_types = {ticker: instrument_type for ticker, instrument_type in self.connection.execute(
            "SELECT ticker, instrument_type FROM security_master WHERE ticker IN (" + ",".join("?" for _ in rule.tickers) + ")",
            list(rule.tickers),
        ).fetchall()}
        missing = [ticker for ticker in rule.tickers if ticker not in caps and security_types.get(ticker) != "ETF"]
        if missing:
            raise ValueError("fixed universe tickers missing from market-cap snapshot: " + ", ".join(missing))
        snapshot_id = str(uuid4())
        self.connection.execute(
            "INSERT INTO universe_snapshots (universe_snapshot_id, group_name, as_of_trade_date, "
            "market_cap_snapshot_id, rule_version, rule_json, created_at, listing_snapshot_id) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            [snapshot_id, rule.group_name, as_of_trade_date, market_cap_snapshot_id,
             rule.rule_version, rule.as_json(), created_at],
        )
        self.connection.executemany("INSERT INTO universe_members VALUES (?, ?, ?, ?, ?)", [
            (snapshot_id, ticker, caps.get(ticker, Decimal("0")), Decimal("0"), rank)
            for rank, ticker in enumerate(rule.tickers, start=1)
        ])
        return snapshot_id

    def member_tickers(self, universe_snapshot_id: str) -> set[str]:
        return {row[0] for row in self.connection.execute(
            "SELECT ticker FROM universe_members WHERE universe_snapshot_id = ?", [universe_snapshot_id]
        ).fetchall()}

    def members_by_trade_date(self, group_name: str, start_date: date, end_date: date) -> dict[date, set[str]]:
        """Return the immutable membership set that was known on each historical date."""
        rows = self.connection.execute(
            "SELECT u.as_of_trade_date, m.ticker FROM universe_snapshots u "
            "JOIN universe_members m ON m.universe_snapshot_id = u.universe_snapshot_id "
            "WHERE u.group_name = ? AND u.as_of_trade_date BETWEEN ? AND ? "
            "ORDER BY u.as_of_trade_date, m.ticker",
            [group_name, start_date, end_date],
        ).fetchall()
        members: dict[date, set[str]] = {}
        for trade_date, ticker in rows:
            members.setdefault(trade_date, set()).add(ticker)
        return members
