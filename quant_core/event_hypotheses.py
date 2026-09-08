"""Deterministic gates around LLM-produced macro-event hypotheses."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from uuid import uuid4


QUALITY_OFFICIAL = "AUTHORITATIVE_OFFICIAL"
QUALITY_MULTI = "MULTI_INDEPENDENT_SOURCE"
QUALITY_SINGLE = "UNVERIFIED_SINGLE_SOURCE"
MAX_INDUSTRY_SCORE = Decimal("0.35")
MAX_TOTAL_ABS_SCORE = Decimal("1.00")


@dataclass(frozen=True)
class EvidenceFact:
    document_id: str
    source_url: str
    published_at: datetime
    received_at: datetime


@dataclass(frozen=True)
class IndustryImpact:
    industry_code: str
    direction: str
    score: Decimal
    expected_duration_days: int
    uncertainty: str


def normalized_domain(source_url: str) -> str:
    """Return a stable registrable-domain approximation for audit gating."""
    host = urlparse(source_url).hostname
    if not host:
        raise ValueError("evidence source_url must contain a hostname")
    parts = host.lower().split(".")
    if len(parts) < 2:
        return parts[0]
    return ".".join(parts[-3:]) if parts[-2:] in (["com", "cn"], ["gov", "cn"], ["org", "cn"]) else ".".join(parts[-2:])


def parse_llm_hypothesis(payload: Mapping[str, Any]) -> tuple[str, tuple[IndustryImpact, ...]]:
    """Accept only the LLM fields; IDs and evidence quality remain system-owned."""
    category, impacts = payload.get("event_category"), payload.get("industry_impacts")
    if not isinstance(category, str) or not category.strip() or not isinstance(impacts, list) or not impacts:
        raise ValueError("macro hypothesis requires event_category and industry_impacts")
    parsed = []
    for item in impacts:
        code, direction, raw_score = item.get("sw_industry_code"), item.get("impact_direction"), item.get("event_score")
        duration, uncertainty = item.get("expected_duration_days"), item.get("uncertainty_and_counter_arguments")
        if not isinstance(code, str) or not code or direction not in {"POSITIVE", "NEGATIVE"}:
            raise ValueError("macro impact contains an invalid industry code or direction")
        if not isinstance(raw_score, (int, float)) or not isinstance(duration, int) or duration <= 0 or not isinstance(uncertainty, str) or not uncertainty.strip():
            raise ValueError("macro impact contains invalid score, duration or uncertainty")
        score = min(MAX_INDUSTRY_SCORE, max(-MAX_INDUSTRY_SCORE, Decimal(str(raw_score))))
        if (direction == "POSITIVE" and score < 0) or (direction == "NEGATIVE" and score > 0):
            raise ValueError("macro impact direction conflicts with event_score")
        parsed.append(IndustryImpact(code, direction, score, duration, uncertainty))
    if len({impact.industry_code for impact in parsed}) != len(parsed):
        raise ValueError("macro hypothesis cannot repeat an industry code")
    total = sum((abs(impact.score) for impact in parsed), Decimal("0"))
    if total > MAX_TOTAL_ABS_SCORE:
        scale = MAX_TOTAL_ABS_SCORE / total
        parsed = [IndustryImpact(item.industry_code, item.direction, (item.score * scale).quantize(Decimal("0.00000001")), item.expected_duration_days, item.uncertainty) for item in parsed]
        residual = MAX_TOTAL_ABS_SCORE - sum((abs(item.score) for item in parsed), Decimal("0"))
        if residual:
            first = parsed[0]
            signed_residual = residual if first.score >= 0 else -residual
            parsed[0] = IndustryImpact(first.industry_code, first.direction, first.score + signed_residual, first.expected_duration_days, first.uncertainty)
    return category, tuple(parsed)


def evidence_quality(evidence: Iterable[EvidenceFact], effective_as_of: datetime, official_domains: Iterable[str]) -> tuple[str, tuple[EvidenceFact, ...]]:
    """Validate the causal clock before classifying independent source evidence."""
    facts = tuple(evidence)
    if not facts:
        raise ValueError("macro hypothesis requires evidence")
    if effective_as_of.tzinfo is None:
        raise ValueError("effective_as_of_timestamp must include a timezone")
    official = {domain.lower() for domain in official_domains}
    domains = set()
    for fact in facts:
        if fact.published_at.tzinfo is None or fact.received_at.tzinfo is None:
            raise ValueError("evidence timestamps must include a timezone")
        if not fact.published_at <= fact.received_at <= effective_as_of:
            raise ValueError("evidence violates the decision causal clock")
        domain = normalized_domain(fact.source_url)
        domains.add(domain)
        if domain in official:
            return QUALITY_OFFICIAL, facts
    return (QUALITY_MULTI if len(domains) >= 2 else QUALITY_SINGLE), facts


def persist_hypothesis(connection, payload: Mapping[str, Any], evidence: Iterable[EvidenceFact], effective_as_of: datetime,
                       taxonomy_version: str, official_domains: Iterable[str], created_at: datetime) -> str:
    """Persist an immutable, shadow-eligible hypothesis only after deterministic gates."""
    category, impacts = parse_llm_hypothesis(payload)
    quality, facts = evidence_quality(evidence, effective_as_of, official_domains)
    valid_codes = {row[0] for row in connection.execute(
        "SELECT industry_code FROM sw_industry_taxonomy WHERE taxonomy_version = ?", [taxonomy_version]
    ).fetchall()}
    status, reason = ("SHADOW_ELIGIBLE", None) if quality != QUALITY_SINGLE else ("REJECTED", "INSUFFICIENT_INDEPENDENT_EVIDENCE")
    if any(impact.industry_code not in valid_codes for impact in impacts):
        status, reason = "REJECTED", "INVALID_INDUSTRY_CODE"
    result = {"event_category": category, "industry_impacts": [impact.__dict__ | {"score": str(impact.score)} for impact in impacts]}
    result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    hypothesis_id = str(uuid4())
    connection.execute("INSERT INTO macro_event_hypotheses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        hypothesis_id, effective_as_of, quality, len({normalized_domain(fact.source_url) for fact in facts}), status,
        reason, result_json, sha256(result_json.encode("utf-8")).hexdigest(), created_at,
    ])
    connection.executemany("INSERT INTO macro_event_evidence VALUES (?, ?)", [(hypothesis_id, fact.document_id) for fact in facts])
    if status == "SHADOW_ELIGIBLE":
        connection.executemany("INSERT INTO macro_event_impacts VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (hypothesis_id, taxonomy_version, impact.industry_code, impact.direction, impact.score,
             impact.expected_duration_days, impact.uncertainty) for impact in impacts
        ])
    return hypothesis_id
