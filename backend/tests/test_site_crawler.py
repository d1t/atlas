"""Unit tests for the website contact crawler.

Uses httpx MockTransport so no real network traffic is made.
"""
from __future__ import annotations

import httpx
import pytest

from app.scrapers.site_crawler import SiteCrawler


def _mock_client(routes: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url).rstrip("/")
        for url, (status, body) in routes.items():
            if key == url.rstrip("/"):
                return httpx.Response(status, text=body)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_extracts_role_email_from_contact_page(monkeypatch):
    transport = _mock_client(
        {
            "https://acmesugar.com/": (
                200,
                "<html><body><a href='/contact'>contact</a></body></html>",
            ),
            "https://acmesugar.com/contact": (
                200,
                (
                    "<html><body>"
                    "<p>Email: <a href='mailto:sales@acmesugar.com'>sales@acmesugar.com</a></p>"
                    "<p>CEO: ceo@acmesugar.com</p>"
                    "<p>Phone: +55 21 99999-0000</p>"
                    "</body></html>"
                ),
            ),
        }
    )

    orig_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    crawler = SiteCrawler()
    out = await crawler.enrich("https://acmesugar.com")
    # Prefer role inbox (sales@) over personal (ceo@) on the same domain.
    assert out.get("email") == "sales@acmesugar.com"
    assert out.get("phone") and "99999" in out["phone"]


@pytest.mark.asyncio
async def test_returns_empty_for_blank_input():
    crawler = SiteCrawler()
    assert await crawler.enrich(None) == {}
    assert await crawler.enrich("") == {}


@pytest.mark.asyncio
async def test_ignores_placeholder_email(monkeypatch):
    transport = _mock_client(
        {
            "https://foo.com/": (
                200,
                "<body>Reach us at your@domain.com or example@example.com</body>",
            ),
            "https://foo.com/contact": (404, ""),
            "https://foo.com/contact-us": (404, ""),
        }
    )
    orig_init = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    crawler = SiteCrawler()
    out = await crawler.enrich("https://foo.com")
    assert out == {} or "email" not in out
