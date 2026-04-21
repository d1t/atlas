"""Web scraper entry point — supplier SERP aggregator.

Backends (all optional, layered):

1. **Tavily Search API** — enabled when `TAVILY_API_KEY` is set.
2. **Brave Search API** — enabled when `BRAVE_API_KEY` is set. Runs
   alongside Tavily and the two result sets are merged (deduped by
   URL); this broadens coverage and reduces dependence on one vendor.
3. **Mock / empty** — default when no key is set. Returns `[]`, which
   causes `SupplierDiscoveryService` to fall back to the offline
   LLM-only synthesis path so the system stays usable without keys.

A full Playwright-backed scraper (Google SERPs, LinkedIn, trade
directories) can drop in here in the future; the interface is the
stable contract.
"""
from __future__ import annotations

import asyncio
import logging

from app.integrations.brave_search import BraveSearch
from app.integrations.tavily import TavilySearch

logger = logging.getLogger(__name__)


class WebScraper:
    def __init__(self) -> None:
        self._tavily = TavilySearch()
        self._brave = BraveSearch()

    @property
    def backend(self) -> str:
        backends = []
        if self._tavily.enabled:
            backends.append("tavily")
        if self._brave.enabled:
            backends.append("brave")
        return "+".join(backends) if backends else "offline"

    async def search_suppliers(
        self, commodity: str, country: str | None = None
    ) -> list[dict]:
        """Return merged, deduped SERP hits from every enabled provider."""
        tasks = []
        if self._tavily.enabled:
            tasks.append(self._tavily.search_suppliers(commodity, country))
        if self._brave.enabled:
            tasks.append(self._brave.search_suppliers(commodity, country))

        if not tasks:
            logger.info(
                "WebScraper offline (no TAVILY_API_KEY / BRAVE_API_KEY); "
                "falling back to LLM-only discovery."
            )
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged: list[dict] = []
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("search backend error: %s", r)
                continue
            merged.extend(r)

        # Dedup by URL, keep first occurrence (preserves ranking order:
        # Tavily first if both enabled).
        seen: set[str] = set()
        deduped: list[dict] = []
        for h in merged:
            url = (h.get("url") or "").strip().rstrip("/")
            if not url or url in seen:
                continue
            seen.add(url)
            deduped.append(h)
        return deduped
