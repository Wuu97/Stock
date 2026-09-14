from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from quant_core.news_text import store_text_extraction
from scripts.analyze_ticker_news import _eligible_documents


def test_text_extraction_is_append_only_and_success_requires_text():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute("INSERT INTO news_documents (document_id, source_channel, scope, published_at, received_at, headline, body, content_sha256, evidence_role, created_at) VALUES ('doc', 'fixture', 'STOCK', ?, ?, 'title', 'body', 'hash', 'EVIDENCE_ELIGIBLE', ?)", [now, now, now])
    store_text_extraction(connection, 'doc', 'https://example.test/a.pdf', 'SUCCESS', now, extracted_text='full text')
    with pytest.raises(Exception):
        store_text_extraction(connection, 'doc', 'https://example.test/a.pdf', 'SUCCESS', now, extracted_text='again')


def test_risk_assessment_prefers_the_latest_successful_pdf_text():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    connection.execute("INSERT INTO news_documents (document_id, source_channel, scope, ticker, published_at, received_at, headline, body, content_sha256, evidence_role, created_at) VALUES ('doc', 'fixture', 'STOCK', '600000.SH', ?, ?, 'title', 'title only', 'hash', 'EVIDENCE_ELIGIBLE', ?)", [now, now, now])
    store_text_extraction(connection, 'doc', 'https://example.test/a.pdf', 'SUCCESS', now, extracted_text='full official disclosure')
    row = _eligible_documents(connection, ('600000.SH',), now, 72)[0]
    assert row[3] == 'full official disclosure'
    assert row[4] == 'EXTRACTED_PDF'
