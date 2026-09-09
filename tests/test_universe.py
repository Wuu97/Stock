from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.models import DayBar
from quant_core.universe import DynamicUniverseRule, UniverseService, select_dynamic_universe


def _bar(day, ticker, close):
    return DayBar(day, ticker, close, close, close, close, 100, Decimal("1000"), Decimal("20"), Decimal("1"))


def test_dynamic_universe_uses_current_cap_and_historical_30_day_return():
    start = date(2026, 7, 1)
    bars = []
    for index in range(31):
        bars.extend([_bar(start + timedelta(days=index), "AAA", Decimal("10") + index),
                     _bar(start + timedelta(days=index), "BBB", Decimal("10") + Decimal(index) / 2),
                     _bar(start + timedelta(days=index), "CCC", Decimal("10") + Decimal(index) * 2)])
    rule = DynamicUniverseRule("large_cap_momentum", Decimal("80000000000"), 30, 2)
    members = select_dynamic_universe(bars, {"AAA": Decimal("90000000000"), "BBB": Decimal("90000000000"),
                                             "CCC": Decimal("70000000000")}, start + timedelta(days=30), rule)
    assert [member.ticker for member in members] == ["AAA", "BBB"]


def test_dynamic_universe_members_are_snapshotted_in_the_database():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    service.store_market_caps("cap", now, "fixture", {"AAA": Decimal("90000000000")}, now)
    bars = [_bar(date(2026, 8, 1) + timedelta(days=index), "AAA", Decimal("10") + index) for index in range(31)]
    snapshot_id = service.create_snapshot(
        "cap", date(2026, 8, 31),
        DynamicUniverseRule("large_cap_momentum", Decimal("80000000000"), 30, 50), bars, now,
    )
    assert service.member_tickers(snapshot_id) == {"AAA"}
    stored = con.execute("SELECT group_name, rule_json FROM universe_snapshots WHERE universe_snapshot_id = ?", [snapshot_id]).fetchone()
    assert stored[0] == "large_cap_momentum"
    assert '"minimum_total_market_cap": "80000000000"' in stored[1]


def test_multiple_group_snapshots_remain_independent():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    service.store_market_caps("cap", now, "fixture", {
        "AAA": Decimal("90000000000"), "BBB": Decimal("30000000000"),
    }, now)
    bars = []
    for index in range(31):
        day = date(2026, 8, 1) + timedelta(days=index)
        bars.extend([_bar(day, "AAA", Decimal("10") + index), _bar(day, "BBB", Decimal("10") + index * 2)])
    large_cap = service.create_snapshot(
        "cap", date(2026, 8, 31),
        DynamicUniverseRule("large_cap_momentum", Decimal("80000000000"), 30, 50), bars, now,
    )
    mid_cap = service.create_snapshot(
        "cap", date(2026, 8, 31),
        DynamicUniverseRule("mid_cap_momentum", Decimal("10000000000"), 30, 50), bars, now,
    )
    assert service.member_tickers(large_cap) == {"AAA"}
    assert service.member_tickers(mid_cap) == {"AAA", "BBB"}


def test_universe_service_loads_members_by_their_historical_trade_date():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    service.store_market_caps("cap", now, "fixture", {"AAA": Decimal("90000000000"), "BBB": Decimal("90000000000")}, now)
    bars = []
    for index in range(32):
        day = date(2026, 8, 1) + timedelta(days=index)
        bars.extend([_bar(day, "AAA", Decimal("10") + index), _bar(day, "BBB", Decimal("10") + index * 2)])
    rule = DynamicUniverseRule("large_cap_momentum", Decimal("1"), 30, 1)
    service.create_snapshot("cap", date(2026, 8, 31), rule, bars, now)
    service.create_snapshot("cap", date(2026, 9, 1), rule, bars, now)

    by_date = service.members_by_trade_date("large_cap_momentum", date(2026, 8, 31), date(2026, 9, 1))
    assert by_date[date(2026, 8, 31)] == {"BBB"}
    assert by_date[date(2026, 9, 1)] == {"BBB"}
