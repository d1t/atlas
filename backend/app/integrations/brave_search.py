"""Brave Search integration.

Brave offers a generous free tier (2,000 queries/month, no credit card).
When `BRAVE_API_KEY` is set, discovery merges Brave results with Tavily
results (deduped by domain) to broaden supplier coverage.

Docs: https://api.search.brave.com/app/documentation/web-search/get-started
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveSearch:
    def __init__(self) -> None:
        settings = get_settings()
        self._key = settings.brave_api_key
        self._max_results = settings.brave_max_results

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def search_suppliers(
        self, commodity: str, country: str | None = None
    ) -> list[dict]:
        """Return raw search hits shaped like the Tavily adapter for pipeline parity."""
        if not self.enabled:
            return []

        geo = f" in {country}" if country else ""
        query = (
            f"{commodity} supplier exporter mill refinery producer{geo} "
            f"contact email website"
        )
        params = {"q": query, "count": self._max_results, "safesearch": "off"}
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._key,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(_BRAVE_URL, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("Brave search failed: %s", exc)
            return []

        hits: list[dict] = []
        for r in (data.get("web", {}) or {}).get("results", []):
            url = r.get("url") or ""
            if not url:
                continue
            hits.append(
                {
                    "url": url,
                    "title": (r.get("title") or "").strip(),
                    "snippet": (r.get("description") or "").strip(),
                    "score": None,
                    "source": "brave",
                }
            )
        logger.info(
            "Brave returned %d hits for %s (%s)", len(hits), commodity, country
        )
        return hits
