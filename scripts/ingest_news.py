"""Archive one chosen news source before any LLM analysis."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Optional

import duckdb

from quant_core.akshare_source import cls_flash_news, stock_announcements, stock_news
from quant_core.news import NewsArchive
from quant_core.world_news_source import gdelt_articles_cached, native_rss_articles_cached


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--source", required=True, choices=("stock", "announcements", "cls", "gdelt", "rss"))
    parser.add_argument("--ticker")
    parser.add_argument("--date")
    parser.add_argument("--query")
    parser.add_argument("--feed-url")
    parser.add_argument("--source-channel")
    parser.add_argument("--cache-dir", default="data/news_cache")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    connection = duckdb.connect(args.db)
    try:
        archive = NewsArchive(connection)
        request_key = _request_key(args)
        try:
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
            else:
                if args.source == "gdelt":
                    if not args.query:
                        raise ValueError("--query is required for GDELT")
                    source_fetch = gdelt_articles_cached(args.query, now, Path(args.cache_dir))
                else:
                    if not args.feed_url or not args.source_channel:
                        raise ValueError("--feed-url and --source-channel are required for RSS")
                    source_fetch = native_rss_articles_cached(args.feed_url, args.source_channel, now, Path(args.cache_dir))
                documents = source_fetch.documents
        except Exception as error:
            archive.record_request(_source_channel(args.source, args.source_channel), request_key, now, 1, False, 0, 0,
                                   error_code=type(error).__name__)
            raise
        if args.source not in {"gdelt", "rss"}:
            documents, artifact_path, artifact_hash = _archive_normalized_source_snapshot(
                documents, args.source, request_key, now, Path(args.cache_dir)
            )
            archive.record_request(_source_channel(args.source), request_key, now, 1, False, 200, 0,
                                   artifact_path, artifact_hash)
        stored = [archive.store_document(document, now) for document in documents]
        if args.source in {"gdelt", "rss"}:
            for attempt, status, backoff, error_code in source_fetch.attempts:
                archive.record_request(_source_channel(args.source, args.source_channel), source_fetch.request_key_sha256, now, attempt,
                                       error_code == "CACHE_HIT", status, backoff, source_fetch.artifact_path,
                                       source_fetch.artifact_sha256, error_code)
    finally:
        connection.close()
    print({"source": args.source, "fetched": len(documents), "stored": len(set(stored))})


def _request_key(args) -> str:
    payload = {"source": args.source, "ticker": args.ticker, "date": args.date, "query": args.query,
               "feed_url": args.feed_url, "source_channel": args.source_channel}
    return sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _source_channel(source: str, rss_source_channel: Optional[str] = None) -> str:
    return {
        "stock": "akshare_stock_news_em", "announcements": "akshare_stock_notice_report",
        "cls": "akshare_cls_flash", "gdelt": "gdelt_doc_2", "rss": rss_source_channel or "native_rss",
    }[source]


def _archive_normalized_source_snapshot(documents, source: str, request_key: str, received_at: datetime,
                                        cache_dir: Path):
    """Persist the exact normalized source payload passed to the news archive.

    AKShare does not expose its upstream HTTP body, so this artifact deliberately
    records the post-adapter source snapshot rather than claiming to be raw HTTP.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = [{
        "source_channel": item.source_channel, "scope": item.scope, "published_at": item.published_at.isoformat(),
        "headline": item.headline, "body": item.body, "ticker": item.ticker, "external_id": item.external_id,
        "source_url": item.source_url,
    } for item in documents]
    path = cache_dir / f"{source}_{received_at:%Y%m%dT%H%M%S}_{request_key[:12]}.normalized.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    artifact_hash = sha256(path.read_bytes()).hexdigest()
    return tuple(replace(item, raw_artifact_path=str(path), raw_artifact_sha256=artifact_hash) for item in documents), str(path), artifact_hash


if __name__ == "__main__":
    main()
