"""Archive one chosen news source before any LLM analysis."""

import argparse
from datetime import datetime, timezone

import duckdb

from quant_core.akshare_source import cls_flash_news, stock_announcements, stock_news
from quant_core.news import NewsArchive
from quant_core.world_news_source import gdelt_articles, reliefweb_reports


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--source", required=True, choices=("stock", "announcements", "cls", "gdelt", "reliefweb"))
    parser.add_argument("--ticker")
    parser.add_argument("--date")
    parser.add_argument("--query")
    parser.add_argument("--reliefweb-app-name")
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
        documents = gdelt_articles(args.query, now)
    else:
        if not args.query or not args.reliefweb_app_name:
            raise ValueError("--query and --reliefweb-app-name are required for ReliefWeb")
        documents = reliefweb_reports(args.query, now, args.reliefweb_app_name)
    connection = duckdb.connect(args.db)
    try:
        archive = NewsArchive(connection)
        stored = [archive.store_document(document, now) for document in documents]
    finally:
        connection.close()
    print({"source": args.source, "fetched": len(documents), "stored": len(set(stored))})


if __name__ == "__main__":
    main()
