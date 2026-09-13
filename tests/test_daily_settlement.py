from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.daily_settlement import settle_frozen_buys, settle_pending_sells
from quant_core.matching import OpenGapPolicy
from quant_core.models import DayBar, FeeModel, OrderIntent
from quant_core.market_data import MarketDataStore
from quant_core.portfolio import PortfolioPolicy
from quant_core.settlement import SettlementService


FEE = FeeModel("cost_v1", Decimal("0"), Decimal("5"), Decimal("0"), Decimal("0"), Decimal("0"))


def _bar(day, ticker):
    return DayBar(day, ticker, Decimal("10"), Decimal("10.5"), Decimal("9.5"), Decimal("10.2"), 1000, Decimal("10000"), Decimal("11"), Decimal("9"))


def test_daily_settlement_fills_frozen_recommendation_once_and_values_account():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    con.execute("INSERT INTO market_data_snapshots VALUES ('market', ?, 'fixture', ?, ?, 'path', 'hash', ?)", [date(2024, 3, 4), now, now, now])
    MarketDataStore(con).store_bars("market", [_bar(date(2024, 3, 4), "600000.SH")])
    con.execute("INSERT INTO feature_snapshots VALUES ('feature', ?, 20, 'hash', 'path', 'hash', ?, ?)", [date(2024, 3, 1), now, now])
    con.execute("INSERT INTO recommendation_runs VALUES ('run', ?, 's', 'v', 'c', 'feature', ?, 'FROZEN', NULL, ?)", [date(2024, 3, 4), now, now])
    con.execute("INSERT INTO recommendation_items VALUES ('item', 'run', '600000.SH', 1, 1, 10, '{}', ?)", [now])
    SettlementService(con).create_account("acct", "test", Decimal("10000"), date(2024, 3, 1))
    outcomes = settle_frozen_buys(con, "acct", "market", date(2024, 3, 4), date(2024, 3, 5), 100, FEE)
    assert outcomes[0].status == "FILLED"
    assert con.execute("SELECT COUNT(*) FROM sim_executions").fetchone()[0] == 1
    assert con.execute("SELECT total_equity FROM sim_nav_daily").fetchone()[0] == Decimal("10015.0000")
    repeated = settle_frozen_buys(con, "acct", "market", date(2024, 3, 4), date(2024, 3, 5), 100, FEE)
    assert repeated[0].status == "SKIPPED"


def test_daily_settlement_never_executes_a_shadow_recommendation_run():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    con.execute("INSERT INTO market_data_snapshots VALUES ('market', ?, 'fixture', ?, ?, 'path', 'hash', ?)", [date(2024, 3, 4), now, now, now])
    MarketDataStore(con).store_bars("market", [_bar(date(2024, 3, 4), "600000.SH")])
    con.execute("INSERT INTO feature_snapshots VALUES ('feature', ?, 20, 'hash', 'path', 'hash', ?, ?)", [date(2024, 3, 1), now, now])
    con.execute("INSERT INTO recommendation_runs VALUES ('shadow', ?, 's', 'v', 'c', 'feature', ?, 'FROZEN', NULL, ?)", [date(2024, 3, 4), now, now])
    con.execute("INSERT INTO recommendation_run_modes VALUES ('shadow', 'SHADOW', ?)", [now])
    con.execute("INSERT INTO recommendation_items VALUES ('shadow-item', 'shadow', '600000.SH', 1, 1, 10, '{}', ?)", [now])
    SettlementService(con).create_account("acct", "test", Decimal("10000"), date(2024, 3, 1))
    outcomes = settle_frozen_buys(con, "acct", "market", date(2024, 3, 4), date(2024, 3, 5), 100, FEE)
    assert outcomes == ()


def test_daily_settlement_records_open_gap_rejection_against_frozen_reference_close():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now, trade_date = datetime.now(timezone.utc), date(2024, 3, 4)
    con.execute("INSERT INTO market_data_snapshots VALUES ('market', ?, 'fixture', ?, ?, 'path', 'hash', ?)", [trade_date, now, now, now])
    MarketDataStore(con).store_bars("market", [DayBar(trade_date, "600000.SH", Decimal("10.31"), Decimal("10.40"), Decimal("10.20"),
                                                   Decimal("10.25"), 1000, Decimal("10000"), Decimal("11"), Decimal("9"))])
    con.execute("INSERT INTO feature_snapshots VALUES ('feature', ?, 20, 'hash', 'path', 'hash', ?, ?)", [date(2024, 3, 1), now, now])
    con.execute("INSERT INTO recommendation_runs VALUES ('run', ?, 's', 'v', 'c', 'feature', ?, 'FROZEN', NULL, ?)", [trade_date, now, now])
    con.execute("INSERT INTO recommendation_items VALUES ('item', 'run', '600000.SH', 1, 1, 10, '{}', ?)", [now])
    SettlementService(con).create_account("acct", "test", Decimal("10000"), date(2024, 3, 1))
    outcomes = settle_frozen_buys(con, "acct", "market", trade_date, date(2024, 3, 5), 100, FEE,
                                   open_gap_policy=OpenGapPolicy(Decimal("0.03"), Decimal("-0.04")))
    assert outcomes[0].status == "REJECTED"
    assert outcomes[0].reason == "OPEN_GAP_UP_TOO_HIGH"
    assert con.execute("SELECT reject_reason_code FROM sim_order_intents WHERE recommendation_item_id = 'item'").fetchone()[0] == "OPEN_GAP_UP_TOO_HIGH"


def test_daily_settlement_uses_only_newest_production_run_and_budgeted_size():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now, later = datetime.now(timezone.utc), datetime.now(timezone.utc) + timedelta(seconds=1)
    trade_date = date(2024, 3, 4)
    con.execute("INSERT INTO market_data_snapshots VALUES ('market', ?, 'fixture', ?, ?, 'path', 'hash', ?)", [trade_date, now, now, now])
    MarketDataStore(con).store_bars("market", [_bar(trade_date, "600000.SH")])
    con.execute("INSERT INTO feature_snapshots VALUES ('feature', ?, 20, 'hash', 'path', 'hash', ?, ?)", [date(2024, 3, 1), now, now])
    for run_id, created_at in (("old", now), ("new", later)):
        con.execute("INSERT INTO recommendation_runs VALUES (?, ?, 's', 'v', 'c', 'feature', ?, 'FROZEN', NULL, ?)",
                    [run_id, trade_date, now, created_at])
        con.execute("INSERT INTO recommendation_items VALUES (?, ?, '600000.SH', 1, 1, 10, '{}', ?)",
                    [f"item-{run_id}", run_id, created_at])
    SettlementService(con).create_account("acct", "test", Decimal("10000"), date(2024, 3, 1))
    outcomes = settle_frozen_buys(con, "acct", "market", trade_date, date(2024, 3, 5), 100, FEE,
                                   PortfolioPolicy.equal_weight(5, Decimal("0.05")))
    assert [(item.recommendation_item_id, item.status) for item in outcomes] == [("item-new", "FILLED")]
    assert con.execute("SELECT target_shares FROM sim_order_intents").fetchone()[0] == 900


def test_daily_settlement_executes_due_risk_sell_without_a_recommendation():
    con = duckdb.connect(":memory:")
    con.execute(Path("sql/schema.sql").read_text())
    now = datetime.now(timezone.utc)
    trade_date = date(2024, 3, 5)
    con.execute("INSERT INTO market_data_snapshots VALUES ('market', ?, 'fixture', ?, ?, 'path', 'hash', ?)", [trade_date, now, now, now])
    sell_bar = DayBar(trade_date, "600000.SH", Decimal("11"), Decimal("11.5"), Decimal("10.5"),
                       Decimal("11.2"), 1000, Decimal("10000"), Decimal("12.1"), Decimal("9.9"))
    MarketDataStore(con).store_bars("market", [sell_bar])
    service = SettlementService(con)
    service.create_account("acct", "test", Decimal("10000"), date(2024, 3, 1))
    buy = OrderIntent("buy", "acct", "600000.SH", date(2024, 3, 1), "BUY", 100)
    service.create_intent(buy)
    service.settle(buy, _bar(date(2024, 3, 1), "600000.SH"), trade_date, FEE)
    service.create_intent(OrderIntent("sell", "acct", "600000.SH", trade_date, "SELL", 100))
    outcomes = settle_pending_sells(con, "acct", "market", trade_date, date(2024, 3, 6), FEE)
    assert outcomes[0].status == "FILLED"
    assert con.execute("SELECT direction FROM sim_executions WHERE intent_id = 'sell'").fetchone()[0] == "SELL"
