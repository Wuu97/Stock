import duckdb

from quant_core.news_migrations import apply_news_migrations


def test_news_v2_migration_upgrades_existing_news_documents_and_demotes_gdelt():
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE TABLE news_documents (source_channel VARCHAR, evidence_role VARCHAR)")
    connection.execute("INSERT INTO news_documents VALUES ('gdelt_doc_2', 'EVIDENCE_ELIGIBLE')")
    apply_news_migrations(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info('news_documents')").fetchall()}
    assert {"publisher", "source_type", "canonical_url", "language", "evidence_role"}.issubset(columns)
    assert connection.execute("SELECT evidence_role FROM news_documents").fetchone()[0] == "DISCOVERY_ONLY"
