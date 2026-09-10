from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from quant_core.event_hypotheses import EvidenceFact, evidence_quality, parse_llm_hypothesis, persist_hypothesis


def _payload(code="220202", score=0.8):
    return {"event_category": "RESOURCE_CONTROL", "industry_impacts": [{"sw_industry_code": code, "impact_direction": "POSITIVE", "event_score": score, "expected_duration_days": 5, "uncertainty_and_counter_arguments": "may be priced in"}]}


def _fact(document_id, domain, now):
    return EvidenceFact(document_id, f"https://{domain}/news", now, now)


def _connection():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 8, 8, tzinfo=timezone.utc)
    connection.execute("INSERT INTO news_documents (document_id, source_channel, scope, published_at, received_at, headline, body, source_url, content_sha256, evidence_role, created_at) VALUES ('one', 'fixture', 'MACRO', ?, ?, 'one', 'one', 'https://one.test', 'a', 'EVIDENCE_ELIGIBLE', ?)", [now, now, now])
    connection.execute("INSERT INTO news_documents (document_id, source_channel, scope, published_at, received_at, headline, body, source_url, content_sha256, evidence_role, created_at) VALUES ('two', 'fixture', 'MACRO', ?, ?, 'two', 'two', 'https://two.test', 'b', 'EVIDENCE_ELIGIBLE', ?)", [now, now, now])
    connection.execute("INSERT INTO sw_industry_taxonomy VALUES ('sw_2026_v1', '220202', '稀有金属', 3)")
    return connection, now


def test_llm_scores_are_clamped_and_total_exposure_is_capped():
    category, impacts = parse_llm_hypothesis({"event_category": "POLICY", "industry_impacts": [
        {"sw_industry_code": "a", "impact_direction": "POSITIVE", "event_score": 1, "expected_duration_days": 1, "uncertainty_and_counter_arguments": "x"},
        {"sw_industry_code": "b", "impact_direction": "POSITIVE", "event_score": 1, "expected_duration_days": 1, "uncertainty_and_counter_arguments": "x"},
        {"sw_industry_code": "c", "impact_direction": "POSITIVE", "event_score": 1, "expected_duration_days": 1, "uncertainty_and_counter_arguments": "x"},
    ]})
    assert category == "POLICY"
    assert sum(abs(item.score) for item in impacts) == Decimal("1.00")
    assert all(abs(item.score) <= Decimal("0.35") for item in impacts)


def test_single_source_is_rejected_but_independent_sources_are_eligible():
    connection, now = _connection()
    single = persist_hypothesis(connection, _payload(), [_fact("one", "relay.example", now)], now, "sw_2026_v1", [], now)
    assert connection.execute("SELECT status, rejection_reason FROM macro_event_hypotheses WHERE hypothesis_id = ?", [single]).fetchone() == ("REJECTED", "INSUFFICIENT_INDEPENDENT_EVIDENCE")
    accepted = persist_hypothesis(connection, _payload(), [_fact("one", "reuters.com", now), _fact("two", "miit.gov.cn", now)], now, "sw_2026_v1", ["miit.gov.cn"], now)
    assert connection.execute("SELECT status, evidence_quality FROM macro_event_hypotheses WHERE hypothesis_id = ?", [accepted]).fetchone() == ("SHADOW_ELIGIBLE", "AUTHORITATIVE_OFFICIAL")


def test_official_subdomain_configuration_is_normalized():
    connection, now = _connection()
    accepted = persist_hypothesis(connection, _payload(), [_fact("one", "news.un.org", now)], now,
                                  "sw_2026_v1", ["news.un.org"], now)
    assert connection.execute("SELECT status, evidence_quality FROM macro_event_hypotheses WHERE hypothesis_id = ?", [accepted]).fetchone() == ("SHADOW_ELIGIBLE", "AUTHORITATIVE_OFFICIAL")


def test_invalid_industry_and_late_evidence_fail_closed():
    connection, now = _connection()
    hypothesis_id = persist_hypothesis(connection, _payload("made-up"), [_fact("one", "reuters.com", now), _fact("two", "apnews.com", now)], now, "sw_2026_v1", [], now)
    assert connection.execute("SELECT status, rejection_reason FROM macro_event_hypotheses WHERE hypothesis_id = ?", [hypothesis_id]).fetchone() == ("REJECTED", "INVALID_INDUSTRY_CODE")
    with pytest.raises(ValueError, match="causal clock"):
        evidence_quality([_fact("one", "reuters.com", now.replace(hour=9))], now, [])
