from datetime import datetime, timedelta, timezone

import duckdb

from quant_core.news import NewsArchive, NewsDocument
from quant_core.news_risk_review import record_review, review_metrics


def test_risk_review_is_append_only_and_uses_latest_verdict():
    connection = duckdb.connect(":memory:")
    connection.execute(open("sql/schema.sql").read())
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    document_id = NewsArchive(connection).store_document(
        NewsDocument("fixture", "STOCK", now, now, "立案公告", "公告明确披露立案", "600000.SH"), now
    )
    assessment_id = NewsArchive(connection).store_assessment(document_id, "RISK_VETO", "fixture", "model", "risk_v1", {
        "ticker": "600000.SH", "risk_level": "HIGH", "flags": ["REGULATORY_INVESTIGATION"],
        "evidence_document_ids": [document_id], "rationale": "正式公告披露立案",
    }, now)
    first = record_review(connection, assessment_id, "UNCERTAIN", "reviewer", "待核实", now)
    second = record_review(connection, assessment_id, "CONFIRMED_RISK", "reviewer", "公告事实明确", now + timedelta(minutes=1))
    assert first != second
    assert connection.execute("SELECT COUNT(*) FROM news_risk_review_events WHERE assessment_id = ?", [assessment_id]).fetchone()[0] == 2
    assert review_metrics(connection) == {
        "high_assessment_count": 1, "reviewed_high_count": 1, "confirmed_risk_count": 1,
        "false_positive_count": 0, "uncertain_count": 0, "review_precision": 1.0,
    }


def test_risk_review_rejects_unknown_or_non_risk_assessments():
    connection = duckdb.connect(":memory:")
    connection.execute(open("sql/schema.sql").read())
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    try:
        record_review(connection, "missing", "CONFIRMED_RISK", "reviewer", "reason", now)
    except ValueError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("missing assessment must be rejected")
