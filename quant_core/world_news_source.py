"""Free global-event sources used as attributable evidence, not trading facts."""

from datetime import datetime
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from .akshare_source import _parse_news_time
from .news import NewsDocument


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


def reliefweb_reports(query: str, received_at: datetime, app_name: str, limit: int = 50) -> tuple[NewsDocument, ...]:
    """Fetch curated conflict/disaster reports from the UN OCHA ReliefWeb API."""
    if not app_name.strip():
        raise ValueError("ReliefWeb app_name is required")
    params = urlencode({"appname": app_name, "limit": limit, "query[value]": query,
                        "fields[include][]": ["title", "body", "date.created", "url", "source.name"]}, doseq=True)
    payload = _get_json("https://api.reliefweb.int/v2/reports?" + params)
    records = payload.get("data", [])
    if not isinstance(records, list):
        raise RuntimeError("ReliefWeb response is missing data")
    documents = []
    for record in records:
        fields = record.get("fields", {})
        if not fields.get("title") or not fields.get("date", {}).get("created"):
            continue
        documents.append(NewsDocument("reliefweb_reports", "MACRO", _parse_news_time(fields["date"]["created"]), received_at,
                                      str(fields["title"]), str(fields.get("body") or fields["title"]),
                                      external_id=str(record.get("id")), source_url=fields.get("url")))
    return tuple(documents)


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
