"""Tests for the DuckDuckGo HTML search adapter and the WebScraper merge."""
from __future__ import annotations

import httpx
import pytest

from app.integrations.ddg_search import DuckDuckGoSearch, _unwrap_ddg
from app.scrapers.web_scraper import WebScraper

_DDG_SAMPLE_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample-mill.com%2Fabout&rut=abc">
    Example Mill — Sugar Producer
  </a>
  <a class="result__snippet" href="https://example-mill.com/about">
    Example Mill is a leading producer of refined sugar based in Brazil.
  </a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fanother-supplier.com%2F&rut=def">
    Another Supplier Co
  </a>
  <a class="result__snippet" href="https://another-supplier.com/">
    Export-ready sugar supplier with FOB terms. Contact sales@another-supplier.com.
  </a>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_ddg_disabled_flag():
    ddg = DuckDuckGoSearch()
    ddg._enabled = False
    assert not ddg.enabled
    assert await ddg.search_suppliers("sugar", "Brazil") == []


@pytest.mark.asyncio
async def test_ddg_parses_html_results(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_DDG_SAMPLE_HTML)

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    ddg = DuckDuckGoSearch()
    ddg._enabled = True
    ddg._max_results = 10
    hits = await ddg.search_suppliers("sugar", "Brazil")
    assert len(hits) == 2
    assert hits[0]["url"] == "https://example-mill.com/about"
    assert hits[0]["source"] == "duckduckgo"
    assert "Example Mill" in hits[0]["title"]
    assert "refined sugar" in hits[0]["snippet"]


@pytest.mark.parametrize("status", [202, 403, 429, 500])
@pytest.mark.asyncio
async def test_ddg_handles_rate_limit(monkeypatch, status):
    """202/4xx/5xx should drop silently to empty — callers fall back to Tavily.

    DDG returns 202 (with a captcha/interstitial body) as a soft rate-limit;
    its body happens to contain result-like markup on some variants, so we
    must check the status code BEFORE parsing.
    """
    # Body intentionally contains a fake DDG result block — if we forget
    # to bail on 202 the regex parser would return a spurious hit.
    poisoned_body = (
        '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2F'
        'captcha-trap.com&rut=x">Fake</a>'
        '<a class="result__snippet" href="x">poisoned</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=poisoned_body)

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    ddg = DuckDuckGoSearch()
    ddg._enabled = True
    assert await ddg.search_suppliers("sugar") == []


def test_unwrap_ddg_redirect():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Ffoo.com%2Fbar&rut=xyz"
    assert _unwrap_ddg(wrapped) == "https://foo.com/bar"


def test_unwrap_ddg_passthrough_direct_url():
    assert _unwrap_ddg("https://foo.com/bar") == "https://foo.com/bar"


def test_unwrap_ddg_rejects_junk():
    assert _unwrap_ddg("") is None
    assert _unwrap_ddg("javascript:alert(1)") is None


@pytest.mark.asyncio
async def test_web_scraper_merges_and_dedupes(monkeypatch):
    """Tavily + DDG both enabled — duplicate URLs collapse to a single hit."""
    scraper = WebScraper()
    scraper._tavily._key = "t"  # type: ignore[attr-defined]
    scraper._ddg._enabled = True  # type: ignore[attr-defined]

    async def fake_tavily(self, commodity, country=None):
        return [
            {
                "url": "https://dup.com",
                "title": "Dup",
                "snippet": "",
                "source": "tavily",
            },
            {
                "url": "https://tavily-only.com",
                "title": "Tavily-only",
                "snippet": "",
                "source": "tavily",
            },
        ]

    async def fake_ddg(self, commodity, country=None):
        return [
            {
                "url": "https://dup.com/",
                "title": "Dup (DDG)",
                "snippet": "",
                "source": "duckduckgo",
            },
            {
                "url": "https://ddg-only.com",
                "title": "DDG-only",
                "snippet": "",
                "source": "duckduckgo",
            },
        ]

    monkeypatch.setattr(
        "app.integrations.tavily.TavilySearch.search_suppliers", fake_tavily
    )
    monkeypatch.setattr(
        "app.integrations.ddg_search.DuckDuckGoSearch.search_suppliers", fake_ddg
    )

    merged = await scraper.search_suppliers("sugar", "Brazil")
    urls = [h["url"] for h in merged]
    assert "https://dup.com" in urls  # tavily version wins (first)
    assert "https://tavily-only.com" in urls
    assert "https://ddg-only.com" in urls
    assert len(urls) == 3  # dup collapsed
