"""Append-only human review for shadow-only disclosure risk assessments."""

from datetime import datetime
from uuid import uuid4


REVIEW_LABELS = {"CONFIRMED_RISK", "FALSE_POSITIVE", "UNCERTAIN"}


def record_review(connection, assessment_id: str, label: str, reviewer: str,
                  rationale: str, created_at: datetime) -> str:
    """Persist a reviewer verdict without mutating the LLM assessment."""
    if label not in REVIEW_LABELS:
        raise ValueError("unsupported risk review label")
    if not reviewer.strip() or not rationale.strip():
        raise ValueError("reviewer and rationale are required")
    assessment = connection.execute(
        "SELECT 1 FROM news_assessments WHERE assessment_id = ? AND task_type = 'RISK_VETO'", [assessment_id]
    ).fetchone()
    if assessment is None:
        raise ValueError("risk assessment is missing")
    review_event_id = str(uuid4())
    connection.execute(
        "INSERT INTO news_risk_review_events VALUES (?, ?, ?, ?, ?, ?)",
        [review_event_id, assessment_id, label, reviewer.strip(), rationale.strip(), created_at],
    )
    return review_event_id


def review_metrics(connection) -> dict:
    """Summarize only HIGH assessments; low/medium coverage is intentionally not claimed."""
    rows = connection.execute(
        "WITH latest AS ("
        " SELECT assessment_id, review_label, ROW_NUMBER() OVER (PARTITION BY assessment_id ORDER BY created_at DESC, review_event_id DESC) AS row_no"
        " FROM news_risk_review_events"
        "), high AS ("
        " SELECT a.assessment_id FROM news_assessments a "
        " WHERE a.task_type = 'RISK_VETO' AND json_extract_string(a.result_json, '$.risk_level') = 'HIGH'"
        ") SELECT COUNT(*), COUNT(l.assessment_id), "
        " COALESCE(SUM(CASE WHEN l.review_label = 'CONFIRMED_RISK' THEN 1 ELSE 0 END), 0), "
        " COALESCE(SUM(CASE WHEN l.review_label = 'FALSE_POSITIVE' THEN 1 ELSE 0 END), 0), "
        " COALESCE(SUM(CASE WHEN l.review_label = 'UNCERTAIN' THEN 1 ELSE 0 END), 0) "
        "FROM high h LEFT JOIN latest l ON l.assessment_id = h.assessment_id AND l.row_no = 1"
    ).fetchone()
    high_count, reviewed_count, confirmed_count, false_positive_count, uncertain_count = rows
    precision = None if not reviewed_count else confirmed_count / reviewed_count
    return {
        "high_assessment_count": high_count,
        "reviewed_high_count": reviewed_count,
        "confirmed_risk_count": confirmed_count,
        "false_positive_count": false_positive_count,
        "uncertain_count": uncertain_count,
        "review_precision": precision,
    }
