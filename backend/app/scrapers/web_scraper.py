"""Web scraper entry point.

Two backends are supported:

1. **Tavily Search API** — live web search. Enabled automatically when
   `TAVILY_API_KEY` is present. Returns real hits with titles and snippets
   that the discovery service feeds into the LLM for structured extraction.
2. **Mock / empty** — default when no key is set. Returns `[]`, which causes
   `SupplierDiscoveryService` to fall back to the offline LLM-only
   synthesis path so the system stays usable without any API keys.

A full Playwright-backed scraper (Google SERPs, LinkedIn, trade directories)
can drop in here in the future; the interface is the stable contract.
"""
from __future__ import annotations

import logging

from app.integrations.tavily import TavilySearch

logger = logging.getLogger(__name__)


class WebScraper:
    def __init__(self) -> None:
        self._tavily = TavilySearch()

    @property
    def backend(self) -> str:
        return "tavily" if self._tavily.enabled else "offline"

    async def search_suppliers(
        self, commodity: str, country: str | None = None
    ) -> list[dict]:
        """Return raw search hits for downstream NLP extraction.

        Each hit: {"url": str, "title": str, "snippet": str, "source": str}
        """
        if self._tavily.enabled:
            return await self._tavily.search_suppliers(commodity, country)
        logger.info(
            "WebScraper offline (no TAVILY_API_KEY); falling back to LLM-only discovery."
        )
        return []
