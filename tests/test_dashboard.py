from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.dashboard import load_activity, load_dashboard, load_news_monitor
from quant_core.news import NewsArchive, NewsDocument
from quant_core.models import FeeModel, OrderIntent
from quant_core.settlement import SettlementService
from tests.test_risk import _bar


def test_dashboard_reads_account_and_position_without_mutating_state():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    service.create_account("acct", "测试账户", Decimal("10000"), date(2026, 1, 2))
    order = OrderIntent("buy", "acct", "600000.SH", date(2026, 1, 2), "BUY", 100)
    service.create_intent(order)
    fee = FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    service.settle(order, _bar(date(2026, 1, 2), Decimal("10")), date(2026, 1, 5), fee)
    data = load_dashboard(connection, "acct")
    assert data["account"]["name"] == "测试账户"
    assert data["positions"][0]["ticker"] == "600000.SH"
    assert data["positions"][0]["unrealized_return"] is None
    assert data["summary"] == {"cash": 9000.0, "market_value": 0.0, "equity": 9000.0, "max_drawdown": 0.0}


def test_activity_is_scoped_to_the_requested_account():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    for account_id in ("a", "b"):
        service.create_account(account_id, account_id, Decimal("1000"), date(2026, 1, 2))
        service.create_intent(OrderIntent(f"{account_id}-buy", account_id, "600000.SH", date(2026, 1, 2), "BUY", 100))
    activity = load_activity(connection, "a")
    assert [row["ticker"] for row in activity["orders"]] == ["600000.SH"]
    assert len(activity["orders"]) == 1


def test_news_monitor_returns_immutable_facts_and_request_audit():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    archive = NewsArchive(connection)
    archive.store_document(NewsDocument("official_fixture", "MACRO", now, now, "事件", "事件正文",
                                        source_url="https://example.gov/news", raw_artifact_sha256="a" * 64), now)
    archive.record_request("official_fixture", "b" * 64, now, 1, False, 200)
    monitor = load_news_monitor(connection)
    assert monitor["documents"][0]["source"] == "official_fixture"
    assert monitor["documents"][0]["artifact_sha256"] == "a" * 64
    assert monitor["requests"][0]["status"] == 200
