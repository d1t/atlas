"""Tests for Brave Search adapter and multi-backend web scraper merge."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.brave_search import BraveSearch
from app.scrapers.web_scraper import WebScraper


@pytest.mark.asyncio
async def test_brave_disabled_without_key():
    brave = BraveSearch()
    brave._key = ""
    assert not brave.enabled
    assert await brave.search_suppliers("sugar", "Brazil") == []


@pytest.mark.asyncio
async def test_brave_parses_results(monkeypatch):
    body = {
        "web": {
            "results": [
                {
                    "url": "https://example-mill.com",
                    "title": "Example Mill",
                    "description": "Sugar mill in Brazil",
                },
                {
                    "url": "https://another-supplier.com",
                    "title": "Another Supplier",
                    "description": "Exporter",
                },
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-subscription-token") == "test-key"
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    brave = BraveSearch()
    brave._key = "test-key"
    brave._max_results = 10
    hits = await brave.search_suppliers("sugar", "Brazil")
    assert len(hits) == 2
    assert hits[0]["source"] == "brave"
    assert hits[0]["url"] == "https://example-mill.com"


@pytest.mark.asyncio
async def test_web_scraper_merges_and_dedupes(monkeypatch):
    """Tavily + Brave both enabled — duplicates (same URL) collapse to one hit."""
    scraper = WebScraper()
    # Force both providers enabled regardless of env.
    scraper._tavily._key = "t"  # type: ignore[attr-defined]
    scraper._brave._key = "b"  # type: ignore[attr-defined]

    async def fake_tavily(self, commodity, country=None):
        return [
            {
                "url": "https://dup.com",
                "title": "Dup",
                "snippet": "t",
                "source": "tavily",
            },
            {
                "url": "https://tavily-only.com",
                "title": "T Only",
                "snippet": "t",
                "source": "tavily",
            },
        ]

    async def fake_brave(self, commodity, country=None):
        return [
            {
                "url": "https://dup.com/",  # trailing slash should still dedup
                "title": "Dup",
                "snippet": "b",
                "source": "brave",
            },
            {
                "url": "https://brave-only.com",
                "title": "B Only",
                "snippet": "b",
                "source": "brave",
            },
        ]

    from app.integrations.tavily import TavilySearch

    monkeypatch.setattr(TavilySearch, "search_suppliers", fake_tavily)
    monkeypatch.setattr(BraveSearch, "search_suppliers", fake_brave)

    merged = await scraper.search_suppliers("sugar")
    urls = [h["url"] for h in merged]
    # Tavily came first, so dup stays tavily-sourced
    assert urls.count("https://dup.com") + urls.count("https://dup.com/") == 1
    assert "https://tavily-only.com" in urls
    assert "https://brave-only.com" in urls
    assert scraper.backend == "tavily+brave"
