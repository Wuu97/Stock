"""Immutable news evidence and validated LLM research conclusions."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Mapping, Optional
from uuid import uuid4


RISK_LEVELS = {"LOW", "MEDIUM", "HIGH"}
RISK_FLAGS = {"REGULATORY_INVESTIGATION", "MAJOR_SHAREHOLDER_REDUCTION", "ACCOUNTING_ALLEGATION", "INDUSTRY_SHOCK"}


@dataclass(frozen=True)
class NewsDocument:
    source_channel: str
    scope: str
    published_at: datetime
    received_at: datetime
    headline: str
    body: str
    ticker: Optional[str] = None
    external_id: Optional[str] = None
    source_url: Optional[str] = None

    def __post_init__(self) -> None:
        if self.scope not in {"STOCK", "INDUSTRY", "MACRO"} or not self.headline.strip() or not self.body.strip():
            raise ValueError("news document scope, headline and body are required")
        if self.published_at.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("news timestamps must include a timezone")

    @property
    def content_sha256(self) -> str:
        payload = f"{self.source_channel}\n{self.headline}\n{self.body}\n{self.source_url or ''}".encode("utf-8")
        return sha256(payload).hexdigest()


@dataclass(frozen=True)
class MacroEventMapping:
    industry_codes: tuple[str, ...]
    causal_chains: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class RiskVetoAssessment:
    ticker: str
    risk_level: str
    flags: tuple[str, ...]
    evidence_document_ids: tuple[str, ...]
    rationale: str

    @property
    def should_veto(self) -> bool:
        return self.risk_level == "HIGH" and bool(self.evidence_document_ids)


def parse_macro_event_mapping(payload: Mapping[str, Any]) -> MacroEventMapping:
    codes, chains, confidence = payload.get("industry_codes"), payload.get("causal_chains"), payload.get("confidence")
    if not isinstance(codes, list) or not codes or not all(isinstance(code, str) and code.strip() for code in codes):
        raise ValueError("LLM macro mapping requires non-empty industry_codes")
    if not isinstance(chains, list) or not chains or not all(isinstance(chain, str) and chain.strip() for chain in chains):
        raise ValueError("LLM macro mapping requires causal_chains")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("LLM macro mapping confidence must be between zero and one")
    return MacroEventMapping(tuple(codes), tuple(chains), float(confidence))


def parse_risk_veto(payload: Mapping[str, Any]) -> RiskVetoAssessment:
    ticker, level, flags = payload.get("ticker"), payload.get("risk_level"), payload.get("flags")
    evidence, rationale = payload.get("evidence_document_ids"), payload.get("rationale")
    if not isinstance(ticker, str) or not ticker.strip() or level not in RISK_LEVELS:
        raise ValueError("LLM risk assessment requires ticker and a valid risk_level")
    if not isinstance(flags, list) or not set(flags).issubset(RISK_FLAGS):
        raise ValueError("LLM risk assessment contains an unsupported flag")
    if not isinstance(evidence, list) or not all(isinstance(item, str) and item for item in evidence):
        raise ValueError("LLM risk assessment requires evidence_document_ids")
    if not isinstance(rationale, str) or not rationale.strip() or level == "HIGH" and not evidence:
        raise ValueError("high-risk assessments require a rationale and evidence")
    return RiskVetoAssessment(ticker, level, tuple(flags), tuple(evidence), rationale)


class NewsArchive:
    """Persistence shell. It never modifies news facts or an LLM's past conclusion."""

    def __init__(self, connection):
        self.connection = connection

    def store_document(self, document: NewsDocument, created_at: datetime) -> str:
        existing = self.connection.execute(
            "SELECT document_id FROM news_documents WHERE source_channel = ? AND content_sha256 = ?",
            [document.source_channel, document.content_sha256],
        ).fetchone()
        if existing:
            return existing[0]
        document_id = str(uuid4())
        self.connection.execute("INSERT INTO news_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            document_id, document.source_channel, document.external_id, document.scope, document.ticker,
            document.published_at, document.received_at, document.headline, document.body, document.source_url,
            document.content_sha256, created_at,
        ])
        return document_id

    def store_assessment(self, document_id: str, task_type: str, provider_name: str, model_name: str,
                         prompt_version: str, result: Mapping[str, Any], created_at: datetime) -> str:
        if task_type not in {"MACRO_EVENT_MAPPING", "RISK_VETO"}:
            raise ValueError("unsupported news assessment task")
        result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assessment_id = str(uuid4())
        self.connection.execute("INSERT INTO news_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            assessment_id, document_id, task_type, provider_name, model_name, prompt_version, result_json,
            sha256(result_json.encode("utf-8")).hexdigest(), created_at,
        ])
        return assessment_id
