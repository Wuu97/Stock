"""Free global-event sources used as attributable evidence, not trading facts."""

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
from time import sleep
from typing import Any, Optional, Tuple
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree

from .akshare_source import _parse_news_time
from .news import NewsDocument


@dataclass(frozen=True)
class GdeltFetch:
    documents: tuple[NewsDocument, ...]
    request_key_sha256: str
    artifact_path: str
    artifact_sha256: str
    attempts: Tuple[Tuple[int, int, float, Optional[str]], ...]


@dataclass(frozen=True)
class NativeRssFetch:
    documents: tuple[NewsDocument, ...]
    request_key_sha256: str
    artifact_path: str
    artifact_sha256: str
    attempts: Tuple[Tuple[int, int, float, Optional[str]], ...]


def gdelt_articles(query: str, received_at: datetime, max_records: int = 50) -> tuple[NewsDocument, ...]:
    """Discover globally reported events through GDELT's public DOC API."""
    if not query.strip() or not 1 <= max_records <= 250:
        raise ValueError("GDELT query is required and max_records must be between one and 250")
    params = urlencode({"query": query, "mode": "artlist", "format": "json", "maxrecords": max_records})
    payload = _get_json("https://api.gdeltproject.org/api/v2/doc/doc?" + params)
    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        raise RuntimeError("GDELT response is missing articles")
    return tuple(NewsDocument("gdelt_doc_2", "MACRO", _parse_news_time(item["seendate"]), received_at,
                              str(item["title"]), str(item["title"]), external_id=str(item["url"]), source_url=str(item["url"]))
                 for item in articles if item.get("title") and item.get("url") and item.get("seendate"))


def gdelt_articles_cached(query: str, received_at: datetime, cache_dir: Path, max_records: int = 50) -> GdeltFetch:
    """Cache one normalized query per UTC hour and audit every retry outcome."""
    normalized = " ".join(query.lower().split())
    if not normalized:
        raise ValueError("GDELT query is required")
    hour = received_at.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    request_key = sha256(f"{normalized}_{hour}".encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"gdelt_{request_key}.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return _gdelt_result(payload, received_at, request_key, cache_path, ((1, 200, 0.0, "CACHE_HIT"),))
    params = urlencode({"query": query, "mode": "artlist", "format": "json", "maxrecords": max_records})
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + params
    attempts = []
    for attempt in range(1, 4):
        try:
            payload = _get_json(url)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            attempts.append((attempt, 200, 0.0, None))
            return _gdelt_result(payload, received_at, request_key, cache_path, tuple(attempts))
        except RuntimeError as error:
            rate_limited = "rate-limited" in str(error)
            backoff = round((2 ** attempt) + (int(request_key[:2], 16) / 255), 3) if rate_limited and attempt < 3 else 0.0
            attempts.append((attempt, 429 if rate_limited else 0, backoff, "RATE_LIMIT" if rate_limited else "REQUEST_FAILED"))
            if not backoff:
                raise RuntimeError("GDELT request failed after audited retries") from error
            sleep(backoff)
    raise RuntimeError("GDELT request failed after audited retries")


def _gdelt_result(payload, received_at, request_key, cache_path, attempts) -> GdeltFetch:
    artifact_hash = sha256(cache_path.read_bytes()).hexdigest()
    articles = payload.get("articles", [])
    documents = tuple(NewsDocument("gdelt_doc_2", "MACRO", _parse_news_time(item["seendate"]), received_at,
                                   str(item["title"]), str(item["title"]), external_id=str(item["url"]),
                                   source_url=str(item["url"]), raw_artifact_path=str(cache_path), raw_artifact_sha256=artifact_hash)
                      for item in articles if item.get("title") and item.get("url") and item.get("seendate"))
    return GdeltFetch(documents, request_key, str(cache_path), artifact_hash, attempts)


def _get_json(url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 429:
            raise RuntimeError("global news source is rate-limited; retry later") from error
        raise RuntimeError(f"global news source returned HTTP {error.code}") from error
    except Exception as error:
        raise RuntimeError("global news source request failed") from error
    if not isinstance(payload, dict):
        raise RuntimeError("global news source returned an invalid JSON object")
    return payload


def native_rss_articles_cached(feed_url: str, source_channel: str, received_at: datetime,
                               cache_dir: Path, max_records: int = 50) -> NativeRssFetch:
    """Archive an official RSS/Atom feed as attributable macro-event evidence."""
    if not feed_url.startswith(("https://", "http://")) or not source_channel.strip():
        raise ValueError("native RSS feed URL and source channel are required")
    if not 1 <= max_records <= 250:
        raise ValueError("max_records must be between one and 250")
    hour = received_at.astimezone(timezone.utc).strftime("%Y%m%dT%H")
    request_key = sha256(f"{feed_url}_{hour}".encode("utf-8")).hexdigest()
    cache_path = cache_dir / f"rss_{request_key}.xml"
    cache_dir.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        return _rss_result(cache_path, feed_url, source_channel, received_at, request_key,
                           ((1, 200, 0.0, "CACHE_HIT"),), max_records)
    try:
        payload = _get_bytes(feed_url)
    except RuntimeError as error:
        raise RuntimeError("native RSS request failed") from error
    cache_path.write_bytes(payload)
    return _rss_result(cache_path, feed_url, source_channel, received_at, request_key,
                       ((1, 200, 0.0, None),), max_records)


def _rss_result(cache_path: Path, feed_url: str, source_channel: str, received_at: datetime,
                request_key: str, attempts, max_records: int) -> NativeRssFetch:
    raw_payload = cache_path.read_bytes()
    artifact_hash = sha256(raw_payload).hexdigest()
    try:
        root = ElementTree.fromstring(_decode_xml(raw_payload))
    except ElementTree.ParseError as error:
        raise RuntimeError("native RSS response is invalid XML") from error
    documents = tuple(_rss_documents(root, feed_url, source_channel, received_at, cache_path,
                                     artifact_hash, max_records))
    return NativeRssFetch(documents, request_key, str(cache_path), artifact_hash, attempts)


def _decode_xml(payload: bytes) -> bytes:
    """Keep the received artifact intact while parsing gzip-encoded feed bodies."""
    return gzip.decompress(payload) if payload.startswith(b"\x1f\x8b") else payload


def _rss_documents(root, feed_url: str, source_channel: str, received_at: datetime, artifact_path: Path,
                   artifact_hash: str, max_records: int):
    entries = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
    for entry in entries[:max_records]:
        title = _xml_text(entry, "title")
        published = _xml_text(entry, "pubDate") or _xml_text(entry, "published") or _xml_text(entry, "updated")
        if not title or not published:
            continue
        try:
            published_at = _parse_rss_time(published)
        except ValueError:
            continue
        link = _xml_text(entry, "link") or _atom_link(entry) or feed_url
        body = _xml_text(entry, "description") or _xml_text(entry, "summary") or _xml_text(entry, "content") or title
        external_id = _xml_text(entry, "guid") or _xml_text(entry, "id") or link
        yield NewsDocument(source_channel, "MACRO", published_at, received_at, title, body,
                           external_id=external_id, source_url=link, raw_artifact_path=str(artifact_path),
                           raw_artifact_sha256=artifact_hash)


def _xml_text(entry, name: str) -> Optional[str]:
    element = entry.find(name)
    if element is None:
        element = entry.find(f"{{*}}{name}")
    return element.text.strip() if element is not None and element.text and element.text.strip() else None


def _atom_link(entry) -> Optional[str]:
    for link in entry.findall("{*}link"):
        href = link.get("href")
        if href:
            return href
    return None


def _parse_rss_time(value: str) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = _parse_news_time(value)
    if parsed.tzinfo is None:
        raise ValueError("native RSS published timestamp has no timezone")
    return parsed


def _get_bytes(url: str) -> bytes:
    try:
        with urlopen(url, timeout=20) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(f"native RSS source returned HTTP {error.code}") from error
    except Exception as error:
        raise RuntimeError("native RSS source request failed") from error
