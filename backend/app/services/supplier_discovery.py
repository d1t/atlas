"""Module 1: Supplier Discovery Engine.

Pipeline:
    discover()  -> uses scrapers + LLM NLP to extract structured candidates
    enrich()    -> optional provider enrichment (Clearbit/OpenCorporates via adapters)
    dedupe()    -> removes duplicates against existing DB records

The production scraper lives in app/scrapers. In the default (mock) mode we
skip live scraping and synthesize plausible candidates via the LLM so the
system works end-to-end without network access or browser drivers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import get_llm
from app.models.supplier import Supplier
from app.scrapers.web_scraper import WebScraper


@dataclass
class SupplierCandidate:
    name: str
    type: str | None = None
    country: str | None = None
    commodity: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    contact_name: str | None = None
    description: str | None = None
    source: str | None = None


class SupplierDiscoveryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.llm = get_llm()
        self.scraper = WebScraper()

    async def discover(
        self, commodity: str, country: str | None, limit: int = 10
    ) -> list[SupplierCandidate]:
        raw_hits = await self.scraper.search_suppliers(commodity=commodity, country=country)
        if raw_hits:
            candidates = await self._extract_with_llm(raw_hits, commodity, country)
        else:
            candidates = await self._llm_only_discovery(commodity, country, limit)

        candidates = await self._dedupe(candidates)
        return candidates[:limit]

    async def _extract_with_llm(
        self, raw_hits: list[dict], commodity: str, country: str | None
    ) -> list[SupplierCandidate]:
        system = (
            "You are a commodity trading research analyst. Extract structured supplier "
            "records from unstructured web search results. Return ONLY a JSON array of "
            "objects with keys: name, type (mill|trader|broker|unknown), country, "
            "commodity, website, email, phone, contact_name, description."
        )
        user = (
            f"Target commodity: {commodity}\n"
            f"Target country: {country or 'any'}\n"
            f"Raw search hits:\n{json.dumps(raw_hits, indent=2)}\n\n"
            "Output: JSON array."
        )
        result = await self.llm.json(system, user, max_tokens=2000)
        items = result if isinstance(result, list) else result.get("suppliers", [])
        return [self._to_candidate(x, commodity, country) for x in items if isinstance(x, dict)]

    async def _llm_only_discovery(
        self, commodity: str, country: str | None, limit: int
    ) -> list[SupplierCandidate]:
        system = (
            "You simulate a commodity supplier discovery service. Return a JSON array of "
            f"up to {limit} plausible suppliers. Each object must include: name, type, "
            "country, commodity, website, email, description, source."
        )
        user = f"commodity: {commodity}\ncountry: {country or 'global'}\nlist suppliers"
        result = await self.llm.json(system, user, max_tokens=2000)
        items = result if isinstance(result, list) else result.get("suppliers", [])
        if not items and "raw" in result:
            return []
        return [self._to_candidate(x, commodity, country) for x in items if isinstance(x, dict)]

    def _to_candidate(
        self, obj: dict, commodity: str, country: str | None
    ) -> SupplierCandidate:
        return SupplierCandidate(
            name=str(obj.get("name", "")).strip() or "Unknown Supplier",
            type=(obj.get("type") or "unknown"),
            country=obj.get("country") or country,
            commodity=obj.get("commodity") or commodity,
            website=obj.get("website"),
            email=obj.get("email"),
            phone=obj.get("phone"),
            contact_name=obj.get("contact_name"),
            description=obj.get("description"),
            source=obj.get("source") or "discovery",
        )

    async def _dedupe(self, candidates: list[SupplierCandidate]) -> list[SupplierCandidate]:
        if not candidates:
            return []
        names = [c.name.lower() for c in candidates]
        result = await self.db.execute(select(Supplier.name))
        existing = {row.lower() for (row,) in result.all()}

        unique: list[SupplierCandidate] = []
        seen_in_batch: set[str] = set()
        for c in candidates:
            key = c.name.lower()
            if key in existing or key in seen_in_batch:
                continue
            unique.append(c)
            seen_in_batch.add(key)
        _ = names
        return unique

    async def persist(self, candidates: list[SupplierCandidate]) -> list[Supplier]:
        created: list[Supplier] = []
        for c in candidates:
            supplier = Supplier(
                name=c.name,
                type=c.type,
                country=c.country,
                commodity=c.commodity,
                website=c.website,
                email=c.email,
                phone=c.phone,
                contact_name=c.contact_name,
                description=c.description,
                source=c.source,
            )
            self.db.add(supplier)
            created.append(supplier)
        await self.db.flush()
        return created
