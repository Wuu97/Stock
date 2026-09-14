"""Append-only text extraction records for immutable news facts."""

from datetime import datetime
from hashlib import sha256
from typing import Optional
from uuid import uuid4


EXTRACTOR_VERSION = "cninfo_pdf_pypdf_v1"


def store_text_extraction(connection, document_id: str, source_url: str, status: str, created_at: datetime,
                          *, artifact_sha256: Optional[str] = None, extracted_text: Optional[str] = None,
                          error_code: Optional[str] = None, extractor_version: str = EXTRACTOR_VERSION) -> str:
    if status not in {"SUCCESS", "OCR_REQUIRED", "RETRYABLE_FAILURE"} or created_at.tzinfo is None:
        raise ValueError("text extraction status and timezone-aware timestamp are required")
    if status == "SUCCESS" and not extracted_text:
        raise ValueError("successful extraction requires text")
    text_hash = None if extracted_text is None else sha256(extracted_text.encode("utf-8")).hexdigest()
    extraction_id = str(uuid4())
    connection.execute("INSERT INTO news_document_text_extractions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       [extraction_id, document_id, extractor_version, status, source_url, artifact_sha256,
                        extracted_text, text_hash, error_code, created_at])
    return extraction_id
