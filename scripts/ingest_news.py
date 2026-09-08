"""Archive one chosen news source before any LLM analysis."""

import argparse
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from quant_core.akshare_source import cls_flash_news, stock_announcements, stock_news
from quant_core.news import NewsArchive
from quant_core.world_news_source import gdelt_articles_cached


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--source", required=True, choices=("stock", "announcements", "cls", "gdelt"))
    parser.add_argument("--ticker")
    parser.add_argument("--date")
    parser.add_argument("--query")
    parser.add_argument("--cache-dir", default="data/news_cache")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if args.source == "stock":
        if not args.ticker:
            raise ValueError("--ticker is required for stock news")
        documents = stock_news(args.ticker, now)
    elif args.source == "announcements":
        if not args.date:
            raise ValueError("--date is required for announcements")
        documents = stock_announcements(args.date, now)
    elif args.source == "cls":
        documents = cls_flash_news(now)
    elif args.source == "gdelt":
        if not args.query:
            raise ValueError("--query is required for GDELT")
        gdelt_fetch = gdelt_articles_cached(args.query, now, Path(args.cache_dir))
        documents = gdelt_fetch.documents
    connection = duckdb.connect(args.db)
    try:
        archive = NewsArchive(connection)
        stored = [archive.store_document(document, now) for document in documents]
        if args.source == "gdelt":
            for attempt, status, backoff, error_code in gdelt_fetch.attempts:
                archive.record_request("gdelt_doc_2", gdelt_fetch.request_key_sha256, now, attempt,
                                       error_code == "CACHE_HIT", status, backoff, gdelt_fetch.artifact_path,
                                       gdelt_fetch.artifact_sha256, error_code)
    finally:
        connection.close()
    print({"source": args.source, "fetched": len(documents), "stored": len(set(stored))})


if __name__ == "__main__":
    main()
