"""Tavily Search integration.

Tavily is an AI-friendly web search API. It returns real web results with
short answer summaries, which we feed to the LLM for structured supplier
extraction.

If `TAVILY_API_KEY` is not set the `TavilySearch.enabled` flag is False and
callers fall back to the offline mock path.

Docs: https://docs.tavily.com/docs/rest-api/api-reference
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"


class TavilySearch:
    def __init__(self) -> None:
        settings = get_settings()
        self._key = settings.tavily_api_key
        self._max_results = settings.tavily_max_results

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def search_suppliers(
        self, commodity: str, country: str | None = None
    ) -> list[dict]:
        """Return raw search hits for the discovery pipeline.

        Each hit: {"url": str, "title": str, "snippet": str, "source": "tavily"}
        """
        if not self.enabled:
            return []

        geo = f" in {country}" if country else ""
        query = (
            f"{commodity} suppliers exporters mills{geo} "
            f"company contact email website"
        )
        payload: dict[str, Any] = {
            "api_key": self._key,
            "query": query,
            "search_depth": "advanced",
            "max_results": self._max_results,
            "include_answer": False,
            "include_raw_content": False,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(_TAVILY_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("Tavily search failed: %s", exc)
            return []

        hits: list[dict] = []
        for r in data.get("results", []):
            url = r.get("url") or ""
            hits.append(
                {
                    "url": url,
                    "title": (r.get("title") or "").strip(),
                    "snippet": (r.get("content") or "").strip(),
                    "score": r.get("score"),
                    "source": "tavily",
                }
            )
        logger.info("Tavily returned %d hits for %s (%s)", len(hits), commodity, country)
        return hits
