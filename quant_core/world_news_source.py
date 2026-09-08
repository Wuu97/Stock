"""Free global-event sources used as attributable evidence, not trading facts."""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from time import sleep
from typing import Any, Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from .akshare_source import _parse_news_time
from .news import NewsDocument


@dataclass(frozen=True)
class GdeltFetch:
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
