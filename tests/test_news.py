from datetime import datetime, timezone
import gzip
import json

import duckdb
import pytest

from quant_core.news import NewsArchive, NewsDocument, parse_macro_event_mapping, parse_risk_veto
from quant_core.news_risk import validated_risk_veto
from quant_core import world_news_source
from quant_core.cninfo_source import _result as cninfo_result
from quant_core.world_news_source import GdeltRequestError, gdelt_articles_cached, native_rss_articles_cached


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


def test_risk_veto_cannot_change_ticker_or_cite_another_document():
    payload = {"ticker": "600000.SH", "risk_level": "HIGH", "flags": ["REGULATORY_INVESTIGATION"],
               "evidence_document_ids": ["doc-1"], "rationale": "公告明确披露立案"}
    assert validated_risk_veto(payload, "600000.SH", "doc-1").should_veto
    with pytest.raises(ValueError, match="ticker"):
        validated_risk_veto(payload, "600001.SH", "doc-1")
    with pytest.raises(ValueError, match="outside"):
        validated_risk_veto(payload, "600000.SH", "doc-2")


def test_macro_mapping_contract_rejects_unstructured_output():
    mapping = parse_macro_event_mapping({"industry_codes": ["801730.SI"], "causal_chains": ["运价上升→油运受益"], "confidence": 0.7})
    assert mapping.industry_codes == ("801730.SI",)
    with pytest.raises(ValueError, match="industry_codes"):
        parse_macro_event_mapping({"industry_codes": [], "causal_chains": [], "confidence": 2})


def test_gdelt_six_hour_cache_creates_documents_with_raw_artifact(tmp_path):
    now = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    key = __import__("hashlib").sha256(b"middle east conflict_20260908T06").hexdigest()
    path = tmp_path / f"gdelt_{key}.json"
    path.write_text(json.dumps({"articles": [{"title": "Event", "url": "https://example.test/a", "seendate": "20260908T090000Z"}]}))
    result = gdelt_articles_cached("Middle   East Conflict", now, tmp_path)
    assert result.attempts[0][3] == "CACHE_HIT"
    assert result.documents[0].raw_artifact_sha256 == result.artifact_sha256


def test_gdelt_429_starts_global_cooldown_without_retries(tmp_path, monkeypatch):
    now = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    calls = []

    def rate_limited(_url):
        calls.append(_url)
        raise RuntimeError("global news source is rate-limited; retry later")

    monkeypatch.setattr(world_news_source, "_get_json", rate_limited)
    with pytest.raises(GdeltRequestError) as first:
        gdelt_articles_cached("geopolitics", now, tmp_path)
    assert first.value.attempts == ((1, 429, 21600.0, "RATE_LIMIT"),)

    with pytest.raises(GdeltRequestError) as second:
        gdelt_articles_cached("natural disaster", now, tmp_path)
    assert second.value.attempts[0][3] == "COOLDOWN_ACTIVE"
    assert calls and len(calls) == 1


def test_cninfo_documents_are_official_stock_evidence(tmp_path):
    now = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    artifact = tmp_path / "cninfo.json"
    artifact.write_text(json.dumps({"pages": [{"announcements": [{
        "secCode": "688001", "announcementId": "x", "announcementTitle": "重大事项公告",
        "announcementTime": 1788858000000, "adjunctUrl": "finalpage/2026-09-08/x.PDF",
    }]}]}), encoding="utf-8")
    result = cninfo_result(json.loads(artifact.read_text()), now, "key", artifact, ((1, 200, 0.0, None),))
    document = result.documents[0]
    assert document.ticker == "688001.SH"
    assert document.source_type == "OFFICIAL_DISCLOSURE"
    assert document.evidence_role == "EVIDENCE_ELIGIBLE"
    assert document.source_url == "https://static.cninfo.com.cn/finalpage/2026-09-08/x.PDF"


def test_native_rss_hour_cache_creates_attributable_macro_documents(tmp_path):
    now = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
    feed_url = "https://news.example.test/rss.xml"
    key = __import__("hashlib").sha256(f"{feed_url}_20260908T10".encode("utf-8")).hexdigest()
    path = tmp_path / f"rss_{key}.xml"
    path.write_bytes(gzip.compress(b"""<rss><channel><item><title>Official update</title><link>https://news.example.test/a</link><guid>1</guid><pubDate>Mon, 08 Sep 2026 09:00:00 +0000</pubDate><description>Details</description></item></channel></rss>"""))
    result = native_rss_articles_cached(feed_url, "official_fixture", now, tmp_path)
    assert result.attempts[0][3] == "CACHE_HIT"
    assert result.documents[0].source_url == "https://news.example.test/a"
    assert result.documents[0].raw_artifact_sha256 == result.artifact_sha256
