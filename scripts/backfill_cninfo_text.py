"""Append PDF text extractions for recent CNINFO facts without modifying those facts."""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quant_core.cninfo_source import cninfo_pdf_document_cached
from quant_core.database import writer_connection
from quant_core.news import NewsDocument
from quant_core.news_text import store_text_extraction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--latest-days", type=int, default=90)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--cache-dir", default="data/top50/news_cache")
    args = parser.parse_args()
    if args.latest_days <= 0:
        raise ValueError("latest-days must be positive")
    now = datetime.now(timezone.utc)
    connection = writer_connection(args.db, transaction=False)
    try:
        predicates = ["n.source_channel = 'cninfo_announcements'", "n.received_at >= ?", "x.document_id IS NULL"]
        values = [now - timedelta(days=args.latest_days)]
        if args.ticker:
            predicates.append("n.ticker IN (" + ",".join("?" for _ in args.ticker) + ")")
            values.extend(sorted(set(args.ticker)))
        rows = connection.execute(
            "SELECT n.document_id, n.source_channel, n.scope, n.published_at, n.received_at, n.headline, n.body, n.ticker, n.external_id, n.source_url "
            "FROM news_documents n LEFT JOIN news_document_text_extractions x ON x.document_id = n.document_id "
            "WHERE " + " AND ".join(predicates) + " ORDER BY n.received_at", values).fetchall()
        outcomes = {"success": 0, "ocr_required": 0, "retryable_failure": 0}
        for row in rows:
            document_id, *fields = row
            document = NewsDocument(*fields)
            try:
                result = cninfo_pdf_document_cached(document, Path(args.cache_dir))
                store_text_extraction(connection, document_id, document.source_url, "SUCCESS", now,
                                      artifact_sha256=result.artifact_sha256, extracted_text=result.document.body)
                outcomes["success"] += 1
            except Exception as error:
                status = "OCR_REQUIRED" if "no extractable text" in str(error) else "RETRYABLE_FAILURE"
                store_text_extraction(connection, document_id, document.source_url, status, now, error_code=type(error).__name__)
                outcomes["ocr_required" if status == "OCR_REQUIRED" else "retryable_failure"] += 1
    finally:
        connection.close()
    print({"attempted": len(rows), **outcomes})


if __name__ == "__main__":
    main()
