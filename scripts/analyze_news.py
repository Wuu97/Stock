"""Run a DeepSeek JSON assessment over one immutable archived news document."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import duckdb

from quant_core.deepseek_provider import DeepSeekProvider
from quant_core.environment import load_env_file
from quant_core.news import NewsArchive, parse_macro_event_mapping, parse_risk_veto


MACRO_PROMPT = """Return json only. Infer a cautious macro-event hypothesis from the supplied evidence.
Schema: {"industry_codes":["string"],"causal_chains":["evidence -> mechanism -> industry"],"confidence":0.0}.
Use only the supplied document; uncertainty must reduce confidence."""
RISK_PROMPT = """Return json only. Assess only documented candidate-stock risk from the supplied evidence.
Schema: {"ticker":"string","risk_level":"LOW|MEDIUM|HIGH","flags":["REGULATORY_INVESTIGATION|MAJOR_SHAREHOLDER_REDUCTION|ACCOUNTING_ALLEGATION|INDUSTRY_SHOCK"],"evidence_document_ids":["document id"],"rationale":"string"}.
HIGH requires the supplied document id as evidence. Do not invent facts."""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--task", choices=("macro", "risk"), required=True)
    args = parser.parse_args()
    load_env_file(Path(".env"))
    connection = duckdb.connect(args.db)
    try:
        row = connection.execute("SELECT ticker, headline, body, published_at, received_at FROM news_documents WHERE document_id = ?", [args.document_id]).fetchone()
        if row is None:
            raise ValueError("news document is missing")
        ticker, headline, body, published_at, received_at = row
        if published_at > received_at:
            raise ValueError("news document violates published_at <= received_at")
        provider = DeepSeekProvider()
        prompt = MACRO_PROMPT if args.task == "macro" else RISK_PROMPT
        evidence = {"document_id": args.document_id, "ticker": ticker, "headline": headline, "body": body}
        result = provider.json_completion(prompt, json.dumps(evidence, ensure_ascii=False))
        if args.task == "macro":
            parse_macro_event_mapping(result)
            task_type = "MACRO_EVENT_MAPPING"
        else:
            parse_risk_veto(result)
            task_type = "RISK_VETO"
        assessment_id = NewsArchive(connection).store_assessment(args.document_id, task_type, "deepseek", provider.model,
                                                                  "news_json_v1", result, datetime.now(timezone.utc))
    finally:
        connection.close()
    print(json.dumps({"assessment_id": assessment_id, "task": args.task}, ensure_ascii=False))


if __name__ == "__main__":
    main()
