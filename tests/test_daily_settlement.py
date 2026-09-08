from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.daily_settlement import settle_frozen_buys
from quant_core.models import DayBar, FeeModel
from quant_core.market_data import MarketDataStore
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
