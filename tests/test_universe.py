from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.models import DayBar
from quant_core.universe import DynamicUniverseRule, FixedUniverseRule, LiquidityUniverseRule, UniverseService, select_dynamic_universe, select_liquidity_universe


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


def test_liquidity_universe_respects_cap_range_amount_and_momentum_filters():
    start = date(2026, 7, 1)
    bars = []
    for index in range(21):
        bars.extend([
            DayBar(start + timedelta(days=index), "HOT", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10") + index / Decimal("2"), 100, Decimal("200000000"), Decimal("20"), Decimal("1")),
            DayBar(start + timedelta(days=index), "ILLIQUID", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10") + index / Decimal("2"), 100, Decimal("1000000"), Decimal("20"), Decimal("1")),
            DayBar(start + timedelta(days=index), "LARGE", Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10") + index / Decimal("2"), 100, Decimal("200000000"), Decimal("20"), Decimal("1")),
        ])
    rule = LiquidityUniverseRule("event", Decimal("2000000000"), Decimal("80000000000"), 20,
                                 Decimal("100000000"), 5, Decimal("0.05"), 10)
    members = select_liquidity_universe(bars, {"HOT": Decimal("5000000000"), "ILLIQUID": Decimal("5000000000"),
                                                "LARGE": Decimal("90000000000")}, start + timedelta(days=20), rule)
    assert [member.ticker for member in members] == ["HOT"]


def test_listing_age_gate_uses_trading_days_and_fails_closed_when_missing():
    start = date(2026, 7, 1)
    days = [start + timedelta(days=index) for index in range(21)]
    bars = [_bar(day, "OLD", Decimal("10") + index) for index, day in enumerate(days)]
    bars.extend(_bar(day, "NEW", Decimal("10") + index) for index, day in enumerate(days))
    rule = DynamicUniverseRule("age_gate", Decimal("1"), 5, 10, min_listing_trading_days=20)
    members = select_dynamic_universe(
        bars, {"OLD": Decimal("10"), "NEW": Decimal("10"), "UNKNOWN": Decimal("10")}, days[-1], rule,
        {"OLD": start, "NEW": days[10]}, days,
    )
    assert [member.ticker for member in members] == ["OLD"]


def test_listing_age_gate_allows_exactly_the_sixtieth_trading_day():
    start = date(2026, 1, 1)
    days = [start + timedelta(days=index) for index in range(61)]
    bars = [_bar(day, ticker, Decimal("10") + index) for ticker in ("SIXTY", "FIFTY_NINE")
            for index, day in enumerate(days)]
    rule = DynamicUniverseRule("age_60", Decimal("1"), 30, 10, min_listing_trading_days=60)
    members = select_dynamic_universe(bars, {"SIXTY": Decimal("10"), "FIFTY_NINE": Decimal("10")}, days[-1], rule,
                                      {"SIXTY": days[1], "FIFTY_NINE": days[2]}, days)
    assert [member.ticker for member in members] == ["SIXTY"]


def test_non_st_gate_excludes_flagged_and_missing_supplier_records():
    start = date(2026, 7, 1)
    bars = [_bar(start + timedelta(days=index), ticker, Decimal("10") + index)
            for ticker in ("SAFE", "ST", "MISSING") for index in range(8)]
    rule = DynamicUniverseRule("st_gate", Decimal("1"), 5, 10, require_non_st=True)
    members = select_dynamic_universe(
        bars, {ticker: Decimal("10") for ticker in ("SAFE", "ST", "MISSING")}, start + timedelta(days=7), rule,
        st_flags={"SAFE": False, "ST": True},
    )
    assert [member.ticker for member in members] == ["SAFE"]


def test_universe_service_fails_closed_when_non_st_evidence_is_not_supplied():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    service.store_market_caps("cap", now, "fixture", {"AAA": Decimal("10")}, now)
    bars = [_bar(date(2026, 8, 1) + timedelta(days=index), "AAA", Decimal("10") + index)
            for index in range(6)]
    with pytest.raises(ValueError, match="st_backfill_run_id is required"):
        service.create_snapshot(
            "cap", date(2026, 8, 6),
            DynamicUniverseRule("st_required", Decimal("1"), 5, 10, require_non_st=True), bars, now,
        )


def test_snapshot_retains_historical_listing_and_st_lineage():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    day = date(2026, 8, 31)
    service.store_market_caps("cap", now, "fixture", {"AAA": Decimal("10")}, now)
    con.execute("INSERT INTO security_listing_snapshots VALUES ('listing_ref', 'historical_listing_fact_reference_v1', 'x', 'h', ?, ?)", [now, now])
    con.execute("INSERT INTO security_listing_values VALUES ('listing_ref', 'AAA', ?, NULL, 'L')", [date(2026, 1, 1)])
    con.execute("INSERT INTO st_history_backfill_runs VALUES ('st_run', 'baostock', ?, ?, 'baostock_is_st_supplier_fact_v1', ?)", [date(2026, 1, 1), day, now])
    con.execute("INSERT INTO st_history_daily VALUES ('st_run', 'AAA', ?, false, 'x', 'h', ?)", [day, now])
    days = [date(2026, 7, 1) + timedelta(days=index) for index in range(62)]
    bars = [_bar(value, "AAA", Decimal("10") + index) for index, value in enumerate(days)]
    snapshot_id = service.create_snapshot("cap", day, DynamicUniverseRule("historical", Decimal("1"), 30, 50,
                                          min_listing_trading_days=60, require_non_st=True), bars, now,
                                          listing_snapshot_id="listing_ref", st_backfill_run_id="st_run", trading_days=days)
    assert con.execute("SELECT listing_snapshot_id, st_backfill_run_id FROM universe_snapshots WHERE universe_snapshot_id=?", [snapshot_id]).fetchone() == ("listing_ref", "st_run")


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


def test_empty_dynamic_universe_remains_a_valid_empty_snapshot():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    service.store_market_caps("cap", now, "fixture", {"AAA": Decimal("10")}, now)
    bars = [_bar(date(2026, 9, 9), "AAA", Decimal("10")), _bar(date(2026, 9, 10), "AAA", Decimal("11"))]
    snapshot_id = service.create_snapshot(
        "cap", date(2026, 9, 10), DynamicUniverseRule("empty", Decimal("100"), 1, 10), bars, now,
    )
    assert service.member_tickers(snapshot_id) == set()


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
    assert large_cap != mid_cap
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


def test_fixed_universe_is_explicit_and_retains_market_cap_snapshot_lineage():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    service = UniverseService(con)
    now = datetime.now(timezone.utc)
    service.store_market_caps("cap", now, "fixture", {"AAA": Decimal("10"), "BBB": Decimal("20")}, now)
    snapshot_id = service.create_fixed_snapshot(
        "cap", date(2026, 9, 10), FixedUniverseRule("current_holdings", ["BBB", "AAA", "BBB"]), now,
    )
    assert service.member_tickers(snapshot_id) == {"AAA", "BBB"}
    stored = con.execute("SELECT rule_version, rule_json FROM universe_snapshots WHERE universe_snapshot_id = ?", [snapshot_id]).fetchone()
    assert stored[0] == "fixed_tickers_v1"
    assert '"membership": "fixed"' in stored[1]
