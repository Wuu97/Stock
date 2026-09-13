"""Deterministic guards around LLM stock-news risk assessments."""

from typing import Any, Mapping

from .news import RiskVetoAssessment, parse_risk_veto


def validated_risk_veto(payload: Mapping[str, Any], ticker: str, document_id: str) -> RiskVetoAssessment:
    """Reject assessments that do not point to exactly the supplied stock-news fact."""
    assessment = parse_risk_veto(payload)
    if assessment.ticker != ticker:
        raise ValueError("risk assessment ticker does not match the archived document")
    if any(evidence_id != document_id for evidence_id in assessment.evidence_document_ids):
        raise ValueError("risk assessment cites evidence outside the supplied document")
    return assessment
