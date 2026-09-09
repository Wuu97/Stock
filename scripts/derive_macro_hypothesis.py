"""Turn archived news evidence into one gated, shadow-only macro hypothesis."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import duckdb

from quant_core.deepseek_provider import DeepSeekProvider
from quant_core.environment import load_env_file
from quant_core.event_hypotheses import EvidenceFact, persist_hypothesis
from quant_core.news import NewsArchive


PROMPT_VERSION = "macro_event_shadow_v2"
PROMPT = """You are a cautious macro-event analyst. Use only the supplied evidence.
Return JSON only. If there is no material, cross-asset or industry event, return {"no_event":true}.
Otherwise return exactly:
{"event_category":"string","industry_impacts":[{"sw_industry_code":"string","impact_direction":"POSITIVE|NEGATIVE","event_score":number,"expected_duration_days":integer,"uncertainty_and_counter_arguments":"string"}]}
Each sw_industry_code must be copied exactly from allowed_industries. If no listed industry is defensibly affected,
return {"no_event":true}. Do not invent evidence, policy facts, prices, or certainty. Scores must be between -0.35 and 0.35.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    evidence_input = parser.add_mutually_exclusive_group(required=True)
    evidence_input.add_argument("--document-id", action="append")
    evidence_input.add_argument("--latest-hours", type=int)
    parser.add_argument("--effective-as-of", required=True, help="ISO-8601 timestamp with timezone")
    parser.add_argument("--taxonomy-version", required=True)
    parser.add_argument("--official-domain", action="append", default=[])
    args = parser.parse_args()
    effective_as_of = _parse_timestamp(args.effective_as_of)
    if effective_as_of.tzinfo is None:
        raise ValueError("--effective-as-of must include a timezone")
    if args.latest_hours is not None and args.latest_hours <= 0:
        raise ValueError("--latest-hours must be positive")
    load_env_file(Path(".env"))
    connection = duckdb.connect(args.db)
    try:
        rows = _load_evidence(connection, args.document_id, args.latest_hours, effective_as_of)
        if not rows:
            raise ValueError("no eligible archived macro news evidence exists")
        evidence = tuple(EvidenceFact(document_id, source_url, published_at, received_at)
                         for document_id, _, _, published_at, received_at, source_url in rows)
        content = [{"document_id": document_id, "headline": headline, "body": body[:4000], "source_url": source_url}
                   for document_id, headline, body, _, _, source_url in rows]
        industries = _load_industries(connection, args.taxonomy_version)
        if not industries:
            raise ValueError("configured taxonomy version contains no industries")
        provider = DeepSeekProvider()
        result = provider.json_completion(PROMPT, json.dumps({"effective_as_of": effective_as_of.isoformat(),
                                                               "allowed_industries": industries, "evidence": content}, ensure_ascii=False))
        archive = NewsArchive(connection)
        now = datetime.now(timezone.utc)
        for document_id, *_ in rows:
            archive.store_assessment(document_id, "MACRO_EVENT_MAPPING", "deepseek", provider.model,
                                     PROMPT_VERSION, result, now)
        if result.get("no_event") is True:
            print(json.dumps({"status": "NO_EVENT", "evidence_count": len(rows)}, ensure_ascii=False))
            return
        hypothesis_id = persist_hypothesis(connection, result, evidence, effective_as_of,
                                           args.taxonomy_version, args.official_domain, now)
    finally:
        connection.close()
    print(json.dumps({"status": "PERSISTED", "hypothesis_id": hypothesis_id, "evidence_count": len(rows)}, ensure_ascii=False))


def _load_evidence(connection, document_ids, latest_hours, effective_as_of):
    fields = "document_id, headline, body, published_at, received_at, source_url"
    if document_ids:
        placeholders = ",".join("?" for _ in document_ids)
        rows = connection.execute(
            f"SELECT {fields} FROM news_documents WHERE document_id IN ({placeholders}) "
            "AND scope = 'MACRO' AND source_url IS NOT NULL ORDER BY received_at",
            document_ids,
        ).fetchall()
    else:
        cutoff = effective_as_of - timedelta(hours=latest_hours)
        rows = connection.execute(
            f"SELECT {fields} FROM news_documents WHERE scope = 'MACRO' AND source_url IS NOT NULL "
            "AND received_at BETWEEN ? AND ? ORDER BY received_at DESC LIMIT 20", [cutoff, effective_as_of]
        ).fetchall()
    return rows


def _parse_timestamp(value: str) -> datetime:
    """Accept ISO-8601 offsets emitted by both Python and the local date utility."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def _load_industries(connection, taxonomy_version: str):
    return [{"sw_industry_code": code, "sw_industry_name": name} for code, name in connection.execute(
        "SELECT industry_code, industry_name FROM sw_industry_taxonomy WHERE taxonomy_version = ? "
        "ORDER BY industry_level DESC, industry_code", [taxonomy_version]
    ).fetchall()]


if __name__ == "__main__":
    main()
