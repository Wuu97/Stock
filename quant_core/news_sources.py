"""Versioned configuration for attributable official macro-news sources."""

from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class OfficialRssSource:
    channel: str
    feed_url: str
    official_domain: str


def load_official_rss_sources(path: Path) -> tuple[OfficialRssSource, ...]:
    """Load enabled official RSS sources and reject ambiguous source attribution."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise ValueError("news source config requires a sources list")
    sources = []
    channels = set()
    for item in payload["sources"]:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        channel, feed_url, domain = item.get("channel"), item.get("feed_url"), item.get("official_domain")
        if not all(isinstance(value, str) and value.strip() for value in (channel, feed_url, domain)):
            raise ValueError("each enabled news source requires channel, feed_url and official_domain")
        parsed = urlparse(feed_url)
        if parsed.scheme != "https" or not parsed.netloc or channel in channels:
            raise ValueError("news sources require unique channels and HTTPS feed URLs")
        normalized_domain = domain.lower().strip()
        if "." not in normalized_domain or "/" in normalized_domain:
            raise ValueError("official_domain must be a hostname")
        channels.add(channel)
        sources.append(OfficialRssSource(channel.strip(), feed_url.strip(), normalized_domain))
    return tuple(sources)
