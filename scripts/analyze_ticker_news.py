"""Assess recent official stock disclosures as a shadow-only risk-veto signal."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from quant_core.database import writer_connection

from quant_core.deepseek_provider import DeepSeekProvider
from quant_core.environment import load_env_file
from quant_core.news import NewsArchive
from quant_core.news_risk import validated_risk_veto


PROMPT_VERSION = "official_stock_risk_v1"
PROMPT = """Return JSON only. Assess only material downside risk documented in this official listed-company disclosure.
Schema: {"ticker":"string","risk_level":"LOW|MEDIUM|HIGH","flags":["REGULATORY_INVESTIGATION|MAJOR_SHAREHOLDER_REDUCTION|ACCOUNTING_ALLEGATION|INDUSTRY_SHOCK"],"evidence_document_ids":["document id"],"rationale":"string"}.
Use HIGH only for an explicit, material negative fact. The supplied document id is the only allowed evidence id. Do not infer facts from a headline."""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--ticker", action="append", required=True)
    parser.add_argument("--effective-as-of", required=True)
    parser.add_argument("--latest-hours", type=int, default=72)
    args = parser.parse_args()
    effective_as_of = datetime.fromisoformat(args.effective_as_of)
    if effective_as_of.tzinfo is None or args.latest_hours <= 0:
        raise ValueError("effective-as-of requires a timezone and latest-hours must be positive")
    load_env_file(Path(".env"))
    connection = writer_connection(args.db, transaction=False)
    try:
        rows = _eligible_documents(connection, tuple(sorted(set(args.ticker))), effective_as_of, args.latest_hours)
        pending = [row for row in rows if not _already_assessed(connection, row[0])]
        provider = DeepSeekProvider() if pending else None
        archive = NewsArchive(connection)
        high_risk = set()
        for document_id, ticker, headline, body in pending:
            payload = provider.json_completion(PROMPT, json.dumps({
                "document_id": document_id, "ticker": ticker, "headline": headline, "body": body,
            }, ensure_ascii=False))
            assessment = validated_risk_veto(payload, ticker, document_id)
            archive.store_assessment(document_id, "RISK_VETO", "deepseek", provider.model, PROMPT_VERSION,
                                     payload, datetime.now(timezone.utc))
            if assessment.should_veto:
                high_risk.add(ticker)
    finally:
        connection.close()
    print(json.dumps({"eligible_document_count": len(rows), "assessed_document_count": len(pending),
                      "high_risk_tickers": sorted(high_risk), "mode": "SHADOW_ONLY"}, ensure_ascii=False))


def _eligible_documents(connection, tickers: tuple[str, ...], effective_as_of: datetime, latest_hours: int):
    placeholders = ",".join("?" for _ in tickers)
    return connection.execute(
        f"SELECT document_id, ticker, headline, body FROM news_documents "
        f"WHERE scope = 'STOCK' AND evidence_role = 'EVIDENCE_ELIGIBLE' AND ticker IN ({placeholders}) "
        "AND published_at <= received_at AND received_at BETWEEN ? AND ? ORDER BY received_at, document_id",
        [*tickers, effective_as_of - timedelta(hours=latest_hours), effective_as_of],
    ).fetchall()


def _already_assessed(connection, document_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM news_assessments WHERE document_id = ? AND task_type = 'RISK_VETO' AND prompt_version = ?",
        [document_id, PROMPT_VERSION],
    ).fetchone() is not None


if __name__ == "__main__":
    main()
