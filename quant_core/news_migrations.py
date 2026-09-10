"""Explicit, idempotent migrations for the evolving news evidence schema."""


NEWS_DOCUMENT_COLUMNS = {
    "raw_artifact_path": "VARCHAR",
    "raw_artifact_sha256": "VARCHAR",
    "publisher": "VARCHAR",
    "source_type": "VARCHAR",
    "canonical_url": "VARCHAR",
    "language": "VARCHAR",
    "evidence_role": "VARCHAR DEFAULT 'EVIDENCE_ELIGIBLE'",
}


def apply_news_migrations(connection) -> None:
    """Upgrade pre-V2 DuckDB files without rewriting immutable news facts."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info('news_documents')").fetchall()}
    for name, definition in NEWS_DOCUMENT_COLUMNS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE news_documents ADD COLUMN {name} {definition}")
    connection.execute(
        "UPDATE news_documents SET evidence_role = 'DISCOVERY_ONLY' "
        "WHERE source_channel = 'gdelt_doc_2' AND (evidence_role IS NULL OR evidence_role = 'EVIDENCE_ELIGIBLE')"
    )
    connection.execute(
        "UPDATE news_documents SET evidence_role = 'EVIDENCE_ELIGIBLE' WHERE evidence_role IS NULL"
    )
    if "source_url" in columns:
        connection.execute(
            "UPDATE news_documents SET canonical_url = source_url WHERE canonical_url IS NULL"
        )
    connection.execute(
        "UPDATE news_documents SET publisher = CASE source_channel "
        "WHEN 'akshare_cls_flash' THEN 'cls.cn' WHEN 'akshare_stock_news_em' THEN 'eastmoney.com' "
        "WHEN 'akshare_stock_notice_report' THEN 'eastmoney.com' WHEN 'native_rss_un_news' THEN 'news.un.org' "
        "ELSE publisher END WHERE publisher IS NULL"
    )
    connection.execute(
        "UPDATE news_documents SET source_type = CASE source_channel "
        "WHEN 'gdelt_doc_2' THEN 'AGGREGATOR' WHEN 'akshare_cls_flash' THEN 'FINANCIAL_MEDIA' "
        "WHEN 'akshare_stock_news_em' THEN 'FINANCIAL_MEDIA' WHEN 'akshare_stock_notice_report' THEN 'OFFICIAL_DISCLOSURE' "
        "WHEN 'native_rss_un_news' THEN 'OFFICIAL_INSTITUTION' ELSE source_type END WHERE source_type IS NULL"
    )
