from datetime import datetime, timezone
from pathlib import Path

import duckdb

from quant_core.news import NewsArchive, NewsDocument
from quant_core.news_clustering import cluster_documents, independent_representatives


def _document(headline, publisher, url, now):
    return NewsDocument("fixture", "MACRO", now, now, headline, headline, source_url=url,
                        canonical_url=url, publisher=publisher, source_type="WIRE_SERVICE")


def test_event_cluster_keeps_independent_representatives_and_marks_reprints():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    archive = NewsArchive(connection)
    document_ids = [
        archive.store_document(_document("Oil supply disruption after Strait closure", "reuters.com", "https://reuters.com/a", now), now),
        archive.store_document(_document("Oil supply disruption hits after Strait closure", "apnews.com", "https://apnews.com/a", now), now),
        archive.store_document(_document("Oil supply disruption after Strait closure", "reuters.com", "https://reuters.com/b", now), now),
    ]
    members = cluster_documents(connection, document_ids, now)
    assert {member.membership_role for member in members} == {"REPRESENTATIVE", "DUPLICATE"}
    cluster_id = members[0].event_cluster_id
    representatives = independent_representatives(connection, cluster_id)
    assert len(representatives) == 2


def test_discovery_only_documents_cannot_enter_event_cluster():
    connection = duckdb.connect(":memory:")
    connection.execute(Path("sql/schema.sql").read_text())
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)
    document_id = NewsArchive(connection).store_document(
        NewsDocument("gdelt_doc_2", "MACRO", now, now, "Discovery", "Discovery", source_url="https://example.test/a",
                     evidence_role="DISCOVERY_ONLY"), now,
    )
    assert cluster_documents(connection, [document_id], now) == ()
