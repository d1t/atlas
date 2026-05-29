"""Hunter.io Domain Search client for contact enrichment.

Wraps the Hunter.io v2 Domain Search endpoint to find verified business
email addresses given a company domain (e.g. ``copersucar.com.br``).

When ``hunter_api_key`` is empty the client returns deterministic mock
contacts so the entire seeding + enrichment flow works end-to-end in
dev/test without network access or a paid subscription.

See https://hunter.io/api-documentation/v2#domain-search
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.hunter.io/v2"

# Prefer contacts with these departments / seniority for trade outreach.
_PREFERRED_DEPARTMENTS = {"executive", "sales", "management"}
_PREFERRED_SENIORITY = {"executive", "senior", "management"}


@dataclass
class HunterContact:
    email: str
    first_name: str | None = None
    last_name: str | None = None
    position: str | None = None
    confidence: int = 0


def _domain_from_url(website: str) -> str:
    """Extract bare domain from a full URL (``https://www.copersucar.com.br`` → ``copersucar.com.br``)."""
    parsed = urlparse(website if "://" in website else f"https://{website}")
    host = (parsed.netloc or parsed.path.split("/")[0]).lower()
    return host.removeprefix("www.")


def _mock_contacts(domain: str) -> list[HunterContact]:
    """Return plausible mock contacts for dev/test."""
    base = domain.split(".")[0]
    return [
        HunterContact(
            email=f"trading@{domain}",
            first_name="Trade",
            last_name="Desk",
            position="Trading Desk",
            confidence=90,
        ),
        HunterContact(
            email=f"sales@{domain}",
            first_name="Sales",
            last_name="Team",
            position="Commercial Manager",
            confidence=85,
        ),
        HunterContact(
            email=f"info@{domain}",
            first_name=base.title(),
            last_name="Inquiry",
            position="General Enquiries",
            confidence=70,
        ),
    ]


def _pick_best(contacts: list[HunterContact]) -> HunterContact | None:
    """Select the most relevant contact for commodity trade outreach."""
    if not contacts:
        return None

    def _score(c: HunterContact) -> tuple[int, int, int]:
        pos = (c.position or "").lower()
        dept_boost = 1 if any(d in pos for d in ("trad", "sales", "commercial", "export")) else 0
        name_boost = 1 if (c.first_name and c.first_name.strip()) else 0
        return (dept_boost, name_boost, c.confidence)

    return max(contacts, key=_score)


class HunterClient:
    """Thin wrapper around Hunter.io v2 Domain Search."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.hunter_api_key
        self._enabled = bool(self._api_key)

    @property
    def is_live(self) -> bool:
        return self._enabled

    async def domain_search(self, website: str) -> list[HunterContact]:
        """Find contacts for a company domain via Hunter.io Domain Search.

        Returns an empty list if the API is unconfigured, the domain is
        not found, or an error occurs (best-effort, never raises).
        """
        domain = _domain_from_url(website)
        if not domain:
            return []

        if not self._enabled:
            logger.debug("Hunter.io API key not set; returning mock contacts for %s", domain)
            return _mock_contacts(domain)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{_API_BASE}/domain-search",
                    params={"domain": domain, "api_key": self._api_key},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "Hunter.io domain-search %s returned %d: %s",
                        domain, resp.status_code, resp.text[:200],
                    )
                    return []
                data = resp.json().get("data", {})
        except httpx.HTTPError as exc:
            logger.warning("Hunter.io request failed for %s: %s", domain, exc)
            return []

        contacts: list[HunterContact] = []
        for entry in data.get("emails", []):
            contacts.append(
                HunterContact(
                    email=entry.get("value", ""),
                    first_name=entry.get("first_name"),
                    last_name=entry.get("last_name"),
                    position=entry.get("position"),
                    confidence=entry.get("confidence", 0),
                )
            )
        return contacts

    async def enrich_domain(self, website: str) -> HunterContact | None:
        """Return the single best contact for a company domain, or None."""
        contacts = await self.domain_search(website)
        return _pick_best(contacts)
