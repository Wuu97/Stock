"""OCR CNINFO text-extraction records previously marked OCR_REQUIRED."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from quant_core.database import writer_connection
from quant_core.news_ocr import OCR_EXTRACTOR_VERSION, ocr_cninfo_pdf
from quant_core.news_text import store_text_extraction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--document-id", action="append", default=[])
    parser.add_argument("--cache-dir", default="data/top50/news_cache")
    parser.add_argument("--max-documents", type=int, default=20,
                        help="Bound one run so OCR cannot delay the daily shadow pipeline indefinitely")
    args = parser.parse_args()
    if args.max_documents <= 0:
        raise ValueError("max-documents must be positive")
    connection = writer_connection(args.db, transaction=False)
    now = datetime.now(timezone.utc)
    try:
        where, values = ["x.extraction_status = 'OCR_REQUIRED'", "done.document_id IS NULL"], []
        if args.document_id:
            where.append("x.document_id IN (" + ",".join("?" for _ in args.document_id) + ")")
            values.extend(args.document_id)
        rows = connection.execute(
            "SELECT x.document_id, x.source_url FROM news_document_text_extractions x LEFT JOIN "
            "(SELECT document_id FROM news_document_text_extractions WHERE extractor_version = ?) done ON done.document_id = x.document_id "
            "WHERE " + " AND ".join(where) + " ORDER BY x.created_at, x.extraction_id LIMIT ?",
            [OCR_EXTRACTOR_VERSION, *values, args.max_documents]).fetchall()
        success = failure = 0
        for document_id, source_url in rows:
            try:
                text, artifact_hash = ocr_cninfo_pdf(source_url, Path(args.cache_dir))
                store_text_extraction(connection, document_id, source_url, "SUCCESS", now, artifact_sha256=artifact_hash,
                                      extracted_text=text, extractor_version=OCR_EXTRACTOR_VERSION)
                success += 1
            except Exception as error:
                store_text_extraction(connection, document_id, source_url, "RETRYABLE_FAILURE", now,
                                      error_code=type(error).__name__, extractor_version=OCR_EXTRACTOR_VERSION)
                failure += 1
    finally:
        connection.close()
    print({"attempted": len(rows), "success": success, "failed": failure})


if __name__ == "__main__":
    main()
