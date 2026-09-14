from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from quant_core.dashboard import _is_historical_account, _recursive_kdj, load_activity, load_dashboard, load_monitoring_research, load_news_monitor
from quant_core.news import NewsArchive, NewsDocument
from quant_core.news_clustering import cluster_documents
from quant_core.news_text import store_text_extraction
from quant_core.news_ocr import OCR_EXTRACTOR_VERSION
from quant_core.models import FeeModel, OrderIntent
from quant_core.security_master import SecurityMasterStore
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
    SecurityMasterStore(connection).upsert((("600000.SH", "浦发银行"),), "fixture", "fixture.json", "a" * 64,
                                           datetime(2026, 1, 5, tzinfo=timezone.utc), datetime(2026, 1, 5, tzinfo=timezone.utc))
    data = load_dashboard(connection, "acct")
    assert data["account"]["name"] == "测试账户"
    assert data["positions"][0]["ticker"] == "600000.SH"
    assert data["positions"][0]["security_name"] == "浦发银行"
    assert data["positions"][0]["unrealized_return"] is None
    assert data["summary"] == {"cash": 9000.0, "market_value": 0.0, "equity": 9000.0, "max_drawdown": 0.0}


def test_dashboard_marks_replay_accounts_as_historical_and_forward_accounts_as_live_views():
    assert _is_historical_account("top50_backtest_2026q3", "历史回测账户")
    assert _is_historical_account("wf_baseline_example", "WF baseline")
    assert not _is_historical_account("top50_forward_account", "Top50 前向模拟账户")


def test_historical_dashboard_prices_are_capped_at_its_last_nav_date():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    service.create_account("top50_backtest_fixture", "历史回测账户", Decimal("10000"), date(2026, 1, 2))
    order = OrderIntent("buy", "top50_backtest_fixture", "600000.SH", date(2026, 1, 2), "BUY", 100)
    service.create_intent(order)
    fee = FeeModel("test", Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    service.settle(order, _bar(date(2026, 1, 2), Decimal("10")), date(2026, 1, 5), fee)
    now = datetime(2026, 1, 6, tzinfo=timezone.utc)
    for snapshot_id, day, close in (("day-5", date(2026, 1, 5), Decimal("11")),
                                    ("day-6", date(2026, 1, 6), Decimal("12"))):
        connection.execute("INSERT INTO market_data_snapshots VALUES (?, ?, 'fixture', ?, ?, 'm', ?, ?)",
                           [snapshot_id, day, now, now, "a" * 64, now])
        connection.execute("INSERT INTO daily_bars VALUES (?, ?, '600000.SH', ?, ?, ?, ?, 100, 1000, NULL, NULL, 'NORMAL')",
                           [snapshot_id, day, close, close, close, close])
    connection.execute("INSERT INTO sim_nav_daily VALUES ('top50_backtest_fixture', ?, 9000, 1100, 10100, 1.01, 0)",
                       [date(2026, 1, 5)])

    data = load_dashboard(connection, "top50_backtest_fixture")
    assert data["is_historical_account"]
    assert data["account_nav_as_of_date"] == "2026-01-05"
    assert data["market_data_as_of_date"] == "2026-01-06"
    assert data["positions"][0]["last_close"] == 11.0
    assert data["positions"][0]["price_date"] == "2026-01-05"


def test_dashboard_only_exposes_fully_constrained_add_limit_from_broker_snapshot():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    SettlementService(connection).create_account("acct", "测试账户", Decimal("1"), date(2026, 1, 2))
    observed = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO external_account_snapshots VALUES ('snap', 'acct', ?, 100000, 20000, 50000, 20000, 0, 1000, 30000, 5, 0.1, 'a.png;b.png;c.png', ?)",
        [observed, observed],
    )
    connection.execute("INSERT INTO external_account_snapshot_holdings VALUES ('snap', '600000.SH', 1000, 1000, 10, 10)")
    data = load_dashboard(connection, "acct")
    position = data["positions"][0]
    assert position["final_add_shares"] == 100
    assert "add_shares" not in position
    assert [case["shock"] for case in position["stress_tests"]] == [-0.03, -0.05, -0.1]
    assert data["account_risk"]["available_margin"] == 30000.0


def test_dashboard_allows_one_board_lot_for_a_small_high_price_holding():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    SettlementService(connection).create_account("acct", "测试账户", Decimal("1"), date(2026, 1, 2))
    observed = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO external_account_snapshots VALUES ('snap', 'acct', ?, 300000, 20000, 50000, 20000, 0, 50000, 30000, 5, 0.1, 'a.png;b.png;c.png', ?)",
        [observed, observed],
    )
    connection.execute("INSERT INTO external_account_snapshot_holdings VALUES ('snap', '603259.SH', 300, 300, 159.9583, 158.86)")
    position = load_dashboard(connection, "acct")["positions"][0]
    assert position["final_add_shares"] == 100
    assert position["prohibition_reasons"] == []


def test_recursive_kdj_uses_prior_k_and_d_values_for_each_completed_bar():
    values = [Decimal(value) for value in range(1, 11)]
    k, d, j = _recursive_kdj(values, values, values)
    assert round(float(k), 6) == 77.777778
    assert round(float(d), 6) == 62.962963
    assert round(float(j), 6) == 107.407407


def test_activity_is_scoped_to_the_requested_account():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    service = SettlementService(connection)
    for account_id in ("a", "b"):
        service.create_account(account_id, account_id, Decimal("1000"), date(2026, 1, 2))
        service.create_intent(OrderIntent(f"{account_id}-buy", account_id, "600000.SH", date(2026, 1, 2), "BUY", 100))
    activity = load_activity(connection, "a")
    assert [row["ticker"] for row in activity["orders"]] == ["600000.SH"]
    assert activity["orders"][0]["security_name"] == "600000.SH"
    assert len(activity["orders"]) == 1


def test_news_monitor_returns_immutable_facts_and_request_audit():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    archive = NewsArchive(connection)
    document_id = archive.store_document(NewsDocument("official_fixture", "MACRO", now, now, "事件", "事件正文",
                                                       source_url="https://example.gov/news", raw_artifact_sha256="a" * 64), now)
    archive.record_request("official_fixture", "b" * 64, now, 1, False, 200)
    cluster_documents(connection, [document_id], now)
    monitor = load_news_monitor(connection)
    assert monitor["documents"][0]["source"] == "official_fixture"
    assert monitor["documents"][0]["artifact_sha256"] == "a" * 64
    assert monitor["requests"][0]["status"] == 200
    assert monitor["clusters"][0]["representative_count"] == 1
    assert monitor["health"]["stock_document_count"] == 0
    assert "NO_STOCK_NEWS" in monitor["health"]["alerts"]
    assert monitor["health"]["cninfo_pdf"]["attempted"] == 0
    assert monitor["health"]["cninfo_ocr"] == {"attempted": 0, "succeeded": 0, "failed": 0, "pending": 0,
                                                "success_rate": None, "last_attempt_at": None, "failure_reasons": []}


def test_news_health_reports_ocr_progress_and_latest_failure_reasons():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    archive = NewsArchive(connection)
    done_id = archive.store_document(NewsDocument("cninfo", "STOCK", now, now, "已完成", "待 OCR", ticker="000001.SZ",
                                                   source_url="https://example.test/done.pdf", raw_artifact_sha256="a" * 64), now)
    pending_id = archive.store_document(NewsDocument("cninfo", "STOCK", now, now, "待处理", "待 OCR", ticker="000002.SZ",
                                                      source_url="https://example.test/pending.pdf", raw_artifact_sha256="b" * 64), now)
    failed_id = archive.store_document(NewsDocument("cninfo", "STOCK", now, now, "失败", "待 OCR", ticker="000003.SZ",
                                                     source_url="https://example.test/failed.pdf", raw_artifact_sha256="c" * 64), now)
    for document_id, url in ((done_id, "https://example.test/done.pdf"), (pending_id, "https://example.test/pending.pdf"), (failed_id, "https://example.test/failed.pdf")):
        store_text_extraction(connection, document_id, url, "OCR_REQUIRED", now)
    store_text_extraction(connection, done_id, "https://example.test/done.pdf", "SUCCESS", now + timedelta(minutes=1),
                          extracted_text="OCR 正文", extractor_version=OCR_EXTRACTOR_VERSION)
    store_text_extraction(connection, failed_id, "https://example.test/failed.pdf", "RETRYABLE_FAILURE", now + timedelta(minutes=2),
                          error_code="TESSERACT_TIMEOUT", extractor_version=OCR_EXTRACTOR_VERSION)
    ocr = load_news_monitor(connection)["health"]["cninfo_ocr"]
    assert ocr["attempted"] == 2
    assert ocr["succeeded"] == 1
    assert ocr["failed"] == 1
    assert ocr["pending"] == 1
    assert ocr["success_rate"] == 0.5
    assert ocr["failure_reasons"] == [{"error": "TESSERACT_TIMEOUT", "count": 1}]


def test_news_health_only_alerts_when_a_source_latest_request_failed():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute("INSERT INTO external_request_audit VALUES ('old-failure', 'fixture', 'a', ?, 1, 429, false, 0, NULL, NULL, 'RATE_LIMIT', ?)", [now, now])
    connection.execute("INSERT INTO external_request_audit VALUES ('recovery', 'fixture', 'b', ?, 1, 200, false, 0, NULL, NULL, NULL, ?)", [now + timedelta(minutes=1), now + timedelta(minutes=1)])
    assert load_news_monitor(connection)["health"]["failed_sources"] == []


def test_monitoring_research_returns_latest_group_snapshot_and_recommendation():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute("INSERT INTO market_cap_snapshots VALUES ('cap', ?, 'fixture', ?)", [now, now])
    connection.execute("INSERT INTO universe_snapshots VALUES ('universe', 'large_cap_momentum', ?, 'cap', 'v1', ?, ?, NULL, NULL)",
                       [date(2026, 9, 11), '{"require_non_st": true}', now])
    connection.execute("INSERT INTO universe_members VALUES ('universe', '600000.SH', 90000000000, 0.1, 1)")
    connection.execute("INSERT INTO feature_snapshots VALUES ('feature', ?, 20, 'h', 'p', 'h', ?, ?)",
                       [date(2026, 9, 11), now, now])
    connection.execute("INSERT INTO recommendation_runs VALUES ('run', ?, 'momentum_trend_large_cap_momentum', 'v1', 'cost', 'feature', ?, 'FROZEN', NULL, ?)",
                       [date(2026, 9, 14), now, now])
    connection.execute("INSERT INTO recommendation_items VALUES ('item', 'run', '600000.SH', 1, 0.9, 10, '{}', ?)", [now])
    research = load_monitoring_research(connection)
    assert research["snapshots"][0]["group"] == "large_cap_momentum"
    assert research["snapshots"][0]["member_count"] == 1
    assert research["recommendations"][0]["ticker"] == "600000.SH"
