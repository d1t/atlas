"""Web scraper skeleton.

The production implementation is expected to use Playwright to render JS-heavy
sites (LinkedIn, trade directories). For the MVP we ship a scaffold with a
clean async interface so scrapers can be swapped in without touching callers.

The default implementation returns an empty list, which forces the discovery
service to fall back to its LLM-only synthesis path. Drop in a real Playwright
implementation by replacing `search_suppliers`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class WebScraper:
    async def search_suppliers(
        self, commodity: str, country: str | None = None
    ) -> list[dict]:
        """Return raw search hits for downstream NLP extraction.

        Each hit: {"url": str, "title": str, "snippet": str, "source": str}

        TODO (production): implement Playwright-backed scraping of Google /
        Bing / trade directories + LinkedIn. Must handle anti-bot, rate limits,
        and rotating proxies. Returning [] here means the discovery service
        will synthesize candidates via the LLM as a fallback.
        """
        logger.info(
            "WebScraper.search_suppliers: placeholder invoked (commodity=%s, country=%s)",
            commodity,
            country,
        )
        return []
