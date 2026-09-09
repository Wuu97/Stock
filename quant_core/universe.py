"""Point-in-time dynamic monitoring universes based on current market cap and past returns."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import json
from typing import Dict, Iterable, List, Mapping
from uuid import uuid4

from .models import DayBar


@dataclass(frozen=True)
class DynamicUniverseRule:
    group_name: str
    min_total_market_cap: Decimal
    momentum_window_days: int
    top_n: int
    rule_version: str = "current_cap_momentum_v1"

    def __post_init__(self) -> None:
        if not self.group_name.strip():
            raise ValueError("group_name cannot be blank")
        if self.min_total_market_cap < 0 or self.momentum_window_days <= 0 or self.top_n <= 0:
            raise ValueError("dynamic universe rule values must be non-negative and non-zero")

    def as_json(self) -> str:
        return json.dumps({
            "minimum_total_market_cap": str(self.min_total_market_cap),
            "momentum_window_days": self.momentum_window_days,
            "ranking_metric": "close_return",
            "top_n": self.top_n,
        }, sort_keys=True)


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    total_market_cap: Decimal
    momentum: Decimal
    rank: int


def select_dynamic_universe(bars: Iterable[DayBar], market_caps: Mapping[str, Decimal],
                            as_of_trade_date: date, rule: DynamicUniverseRule) -> List[UniverseMember]:
    """Use current market cap only for today's pool; returns use bars available by as-of date."""
    by_ticker: Dict[str, List[DayBar]] = {}
    for bar in bars:
        if bar.trade_date <= as_of_trade_date and bar.status == "TRADING":
            by_ticker.setdefault(bar.ticker, []).append(bar)
    candidates = []
    for ticker, market_cap in market_caps.items():
        history = sorted(by_ticker.get(ticker, []), key=lambda bar: bar.trade_date)
        if market_cap < rule.min_total_market_cap or len(history) <= rule.momentum_window_days:
            continue
        current, prior = history[-1].close, history[-rule.momentum_window_days - 1].close
        candidates.append((ticker, market_cap, (current / prior) - Decimal("1")))
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
                        rule: DynamicUniverseRule, bars: Iterable[DayBar], created_at: datetime) -> str:
        values = self.connection.execute("SELECT ticker, total_market_cap FROM market_cap_values WHERE market_cap_snapshot_id = ?",
                                         [market_cap_snapshot_id]).fetchall()
        members = select_dynamic_universe(bars, {ticker: Decimal(str(cap)) for ticker, cap in values},
                                          as_of_trade_date, rule)
        snapshot_id = str(uuid4())
        self.connection.execute("INSERT INTO universe_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [
            snapshot_id,
            rule.group_name,
            as_of_trade_date,
            market_cap_snapshot_id,
            rule.rule_version,
            rule.as_json(),
            created_at,
        ])
        self.connection.executemany("INSERT INTO universe_members VALUES (?, ?, ?, ?, ?)", [
            (snapshot_id, member.ticker, member.total_market_cap, member.momentum, member.rank) for member in members
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
