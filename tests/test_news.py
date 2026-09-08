from datetime import datetime, timezone
import json

import duckdb
import pytest

from quant_core.news import NewsArchive, NewsDocument, parse_macro_event_mapping, parse_risk_veto
from quant_core.world_news_source import gdelt_articles_cached


def test_news_archive_deduplicates_facts_and_keeps_assessment_immutable():
    connection = duckdb.connect(":memory:")
    connection.execute(open("sql/schema.sql").read())
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    document = NewsDocument("fixture", "STOCK", now, now, "公告", "公司发布澄清公告", "600000.SH")
    archive = NewsArchive(connection)
    document_id = archive.store_document(document, now)
    assert archive.store_document(document, now) == document_id
    assessment_id = archive.store_assessment(document_id, "RISK_VETO", "fixture", "model", "risk_v1", {
        "ticker": "600000.SH", "risk_level": "LOW", "flags": [], "evidence_document_ids": [], "rationale": "无高风险事实",
    }, now)
    assert connection.execute("SELECT document_id FROM news_assessments WHERE assessment_id = ?", [assessment_id]).fetchone()[0] == document_id


def test_llm_json_contract_requires_causal_evidence_for_high_risk_veto():
    assessment = parse_risk_veto({"ticker": "600000.SH", "risk_level": "HIGH", "flags": ["REGULATORY_INVESTIGATION"],
                                   "evidence_document_ids": ["doc-1"], "rationale": "公告披露立案"})
    assert assessment.should_veto
    with pytest.raises(ValueError, match="evidence"):
        parse_risk_veto({"ticker": "600000.SH", "risk_level": "HIGH", "flags": [], "evidence_document_ids": [], "rationale": "缺证据"})


def test_macro_mapping_contract_rejects_unstructured_output():
    mapping = parse_macro_event_mapping({"industry_codes": ["801730.SI"], "causal_chains": ["运价上升→油运受益"], "confidence": 0.7})
    assert mapping.industry_codes == ("801730.SI",)
    with pytest.raises(ValueError, match="industry_codes"):
        parse_macro_event_mapping({"industry_codes": [], "causal_chains": [], "confidence": 2})


def test_gdelt_hour_cache_creates_documents_with_raw_artifact(tmp_path):
    now = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    key = __import__("hashlib").sha256(b"middle east conflict_20260908T10").hexdigest()
    path = tmp_path / f"gdelt_{key}.json"
    path.write_text(json.dumps({"articles": [{"title": "Event", "url": "https://example.test/a", "seendate": "20260908T090000Z"}]}))
    result = gdelt_articles_cached("Middle   East Conflict", now, tmp_path)
    assert result.attempts[0][3] == "CACHE_HIT"
    assert result.documents[0].raw_artifact_sha256 == result.artifact_sha256
