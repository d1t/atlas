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
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import asyncio
import logging

from app.ai import get_llm
from app.ai.llm import MockLLM
from app.core.config import get_settings
from app.models.supplier import Supplier
from app.scrapers.site_crawler import SiteCrawler
from app.scrapers.web_scraper import WebScraper

logger = logging.getLogger(__name__)


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
        settings = get_settings()
        self._crawler = SiteCrawler() if settings.site_crawler_enabled else None
        self._crawler_max = settings.site_crawler_max_per_discovery

    async def discover(
        self, commodity: str, country: str | None, limit: int = 10
    ) -> list[SupplierCandidate]:
        raw_hits = await self.scraper.search_suppliers(commodity=commodity, country=country)
        if raw_hits:
            # Real hits from a live search backend (e.g. Tavily). If a real LLM
            # is configured, let it extract structured records; otherwise fall
            # back to a deterministic URL/title parser so the user still sees
            # real company domains rather than synthetic names.
            if isinstance(self.llm, MockLLM):
                candidates = self._heuristic_from_hits(raw_hits, commodity, country)
            else:
                candidates = await self._extract_with_llm(raw_hits, commodity, country)
        else:
            candidates = await self._llm_only_discovery(commodity, country, limit)

        candidates = await self._dedupe(candidates)
        candidates = candidates[:limit]
        # After dedupe, walk each supplier's website to look for a real email
        # and phone on the contact page. Best-effort: missing values stay None.
        await self._enrich_contacts(candidates)
        return candidates

    async def _enrich_contacts(self, candidates: list[SupplierCandidate]) -> None:
        if not self._crawler or not candidates:
            return

        targets = [
            c
            for c in candidates
            if c.website and not c.email
        ][: self._crawler_max]
        if not targets:
            return

        async def _one(c: SupplierCandidate) -> None:
            try:
                out = await self._crawler.enrich(c.website)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("site crawl failed for %s: %s", c.website, exc)
                return
            if out.get("email") and not c.email:
                c.email = out["email"]
            if out.get("phone") and not c.phone:
                c.phone = out["phone"]

        await asyncio.gather(*[_one(c) for c in targets], return_exceptions=True)

    def _heuristic_from_hits(
        self, hits: list[dict], commodity: str, country: str | None
    ) -> list[SupplierCandidate]:
        """Deterministic fallback when we have real search hits but no real LLM.

        Strategy: treat each unique domain as one supplier. Company name is
        taken from the hit title (cleaned) or the domain's middle label.
        Emails and phones are harvested from the snippet if present.
        """
        by_domain: dict[str, dict] = {}
        email_re = re.compile(r"[\w\.\-+]+@[\w\.\-]+\.[a-z]{2,}", re.IGNORECASE)
        phone_re = re.compile(r"(?:\+?\d[\d\-\s()]{7,}\d)")

        for h in hits:
            url = (h.get("url") or "").strip()
            if not url:
                continue
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower().removeprefix("www.")
            if not domain or _is_directory_domain(domain):
                continue
            entry = by_domain.setdefault(
                domain,
                {
                    "name": _clean_title(h.get("title") or "") or _name_from_domain(domain),
                    "website": f"{parsed.scheme or 'https'}://{domain}",
                    "description": (h.get("snippet") or "").strip()[:400],
                    "email": None,
                    "phone": None,
                },
            )
            snippet = h.get("snippet") or ""
            if not entry["email"]:
                m = email_re.search(snippet)
                if m:
                    entry["email"] = m.group(0).lower()
            if not entry["phone"]:
                m = phone_re.search(snippet)
                if m:
                    entry["phone"] = m.group(0).strip()

        return [
            SupplierCandidate(
                name=row["name"],
                type="unknown",
                country=country,
                commodity=commodity,
                website=row["website"],
                email=row["email"],
                phone=row["phone"],
                description=row["description"] or None,
                source="tavily",
            )
            for row in by_domain.values()
        ]

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


# ---------------- module-level helpers ----------------

_DIRECTORY_DOMAINS = {
    "linkedin.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
    "wikipedia.org",
    "alibaba.com",
    "made-in-china.com",
    "tradeindia.com",
    "indiamart.com",
    "europages.com",
    "panjiva.com",
    "importgenius.com",
    "volza.com",
    "reddit.com",
    "quora.com",
    "bloomberg.com",
    "reuters.com",
    "forbes.com",
    "nytimes.com",
}

_GENERIC_TITLE_PARTS = re.compile(
    r"^(contact(\s+us)?|home|homepage|about(\s+us)?|welcome|index|official\s+site|"
    r"products?|services?)$",
    re.IGNORECASE,
)

_TRAILING_SUFFIX = re.compile(
    r"\s*[-|–—]\s*(home|about|contact|homepage|official site|linkedin|facebook).*$",
    re.IGNORECASE,
)


def _is_directory_domain(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in _DIRECTORY_DOMAINS)


def _clean_title(title: str) -> str:
    """Turn a raw SERP title into a usable company name.

    Strips generic leading segments like "Contact Us |" / "Home -" and trailing
    "- Official Site" style suffixes. If the whole title is generic, returns
    empty so the caller can fall back to the domain name.
    """
    t = _TRAILING_SUFFIX.sub("", title).strip()
    # Split on common separators and drop leading generic parts like "Contact Us".
    parts = [p.strip() for p in re.split(r"\s*[|–—]\s*|\s+-\s+", t) if p.strip()]
    while parts and _GENERIC_TITLE_PARTS.match(parts[0]):
        parts.pop(0)
    if not parts:
        return ""
    return parts[0][:120]


def _name_from_domain(domain: str) -> str:
    label = domain.split(".")[0]
    return label.replace("-", " ").title()
