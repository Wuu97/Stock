import json

import pytest

from quant_core.news_sources import load_official_rss_sources


def test_load_official_rss_sources_accepts_enabled_attributable_sources(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [
        {"channel": "policy", "feed_url": "https://policy.example.test/rss", "official_domain": "policy.example.test", "enabled": True},
        {"channel": "disabled", "feed_url": "https://disabled.example.test/rss", "official_domain": "disabled.example.test", "enabled": False},
    ]}), encoding="utf-8")
    assert load_official_rss_sources(path)[0].channel == "policy"


def test_load_official_rss_sources_rejects_non_https_or_duplicate_channels(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": [
        {"channel": "policy", "feed_url": "http://policy.example.test/rss", "official_domain": "policy.example.test"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="HTTPS"):
        load_official_rss_sources(path)


def test_default_source_registry_has_unique_official_channels():
    sources = load_official_rss_sources(__import__("pathlib").Path("config/news_sources.json"))
    assert [source.channel for source in sources] == ["un_news"]
    assert {source.official_domain for source in sources} == {"news.un.org"}
