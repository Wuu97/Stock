"""Deterministic event-score overlay for shadow recommendations only."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping, Tuple

from .event_hypotheses import MAX_TOTAL_ABS_SCORE
from .strategy import Recommendation


@dataclass(frozen=True)
class EventAdjustment:
    score: Decimal
    industry_codes: Tuple[str, ...]
    hypothesis_ids: Tuple[str, ...]


def active_event_adjustments(connection, taxonomy_version: str, as_of_date: date,
                             effective_as_of: datetime) -> Mapping[str, EventAdjustment]:
    """Return bounded ticker adjustments from previously gated event hypotheses."""
    if effective_as_of.tzinfo is None:
        raise ValueError("effective_as_of must include a timezone")
    rows = connection.execute(
        "SELECT m.ticker, i.industry_code, h.hypothesis_id, i.event_score "
        "FROM macro_event_hypotheses h JOIN macro_event_impacts i ON i.hypothesis_id = h.hypothesis_id "
        "JOIN security_industry_memberships m ON m.taxonomy_version = i.taxonomy_version "
        "AND m.industry_code = i.industry_code "
        "WHERE h.status = 'SHADOW_ELIGIBLE' AND i.taxonomy_version = ? "
        "AND h.effective_as_of_timestamp <= ? "
        "AND CAST(h.effective_as_of_timestamp AS DATE) <= ? "
        "AND CAST(h.effective_as_of_timestamp AS DATE) + i.expected_duration_days >= ? "
        "AND m.valid_from <= ? AND (m.valid_to IS NULL OR m.valid_to >= ?)",
        [taxonomy_version, effective_as_of, as_of_date, as_of_date, as_of_date, as_of_date],
    ).fetchall()
    by_industry = defaultdict(lambda: Decimal("0"))
    for _, industry_code, _, score in rows:
        by_industry[industry_code] += Decimal(str(score))
    total = sum((abs(score) for score in by_industry.values()), Decimal("0"))
    scale = min(Decimal("1"), MAX_TOTAL_ABS_SCORE / total) if total else Decimal("1")
    tickers = defaultdict(lambda: {"score": Decimal("0"), "industries": set(), "hypotheses": set()})
    for ticker, industry_code, hypothesis_id, _ in rows:
        tickers[ticker]["score"] += by_industry[industry_code] * scale
        tickers[ticker]["industries"].add(industry_code)
        tickers[ticker]["hypotheses"].add(hypothesis_id)
    return {
        ticker: EventAdjustment(values["score"], tuple(sorted(values["industries"])),
                                tuple(sorted(values["hypotheses"])))
        for ticker, values in tickers.items()
    }


def augment_recommendations(recommendations: Iterable[Recommendation], adjustments: Mapping[str, EventAdjustment],
                            event_weight: Decimal, top_n: int) -> Tuple[Recommendation, ...]:
    """Combine percentile-normalized baseline rank with a bounded event overlay."""
    if not Decimal("0") <= event_weight <= Decimal("1") or top_n <= 0:
        raise ValueError("event_weight must be between zero and one and top_n must be positive")
    candidates = tuple(recommendations)
    percentiles = _percentile_scores(candidates)
    scored = []
    for item in candidates:
        adjustment = adjustments.get(item.ticker, EventAdjustment(Decimal("0"), (), ()))
        raw_score = Decimal(str(item.score))
        base_score = percentiles[item.ticker]
        event_score = max(Decimal("-1"), min(Decimal("1"), adjustment.score / MAX_TOTAL_ABS_SCORE))
        final_score = base_score + event_weight * event_score
        reasons = dict(item.reasons)
        reasons.update({"raw_strategy_score": str(raw_score), "base_percentile": str(base_score),
                        "raw_event_score": str(adjustment.score), "event_normalized_score": str(event_score),
                        "event_weight": str(event_weight), "final_normalized_score": str(final_score),
                        "event_industry_codes": adjustment.industry_codes,
                        "event_hypothesis_ids": adjustment.hypothesis_ids})
        scored.append((item.ticker, final_score, item.close, reasons))
    ranked = sorted(scored, key=lambda item: (item[1], item[0]), reverse=True)[:top_n]
    return tuple(Recommendation(ticker, index, score, close, reasons)
                 for index, (ticker, score, close, reasons) in enumerate(ranked, start=1))


def _percentile_scores(recommendations: Iterable[Recommendation]) -> Mapping[str, Decimal]:
    rows = tuple(recommendations)
    if len(rows) == 1:
        return {rows[0].ticker: Decimal("1")}
    ordered = sorted(rows, key=lambda item: (Decimal(str(item.score)), item.ticker))
    denominator = Decimal(len(ordered) - 1)
    return {item.ticker: Decimal(index) / denominator for index, item in enumerate(ordered)}
