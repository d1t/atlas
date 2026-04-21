"""Lightweight supplier-site crawler for contact enrichment.

Given a supplier's base URL, fetch its homepage + common contact pages
(`/contact`, `/contact-us`, `/contacts`, `/about`) and extract email,
phone, and a plausible contact name from the rendered HTML.

No JS execution — this is requests + regex, so it only catches emails
that are actually in the static HTML. Sites that hide emails behind
contact forms or JS rendering will return nothing, which is fine: we
fall back to whatever Tavily/Brave snippets gave us.

Kept intentionally small so it can run on every discovered supplier
without blowing the request budget or timing out the API call.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_CONTACT_PATHS = ("/", "/contact", "/contact-us", "/contacts", "/about")

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s().]{7,}\d)")
_MAILTO_RE = re.compile(r'mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})', re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

_GENERIC_LOCAL_PARTS = {
    "example",
    "your",
    "youremail",
    "domain",
    "test",
    "demo",
    "sample",
    "user",
    "name",
}

# Prefer "role" inboxes over personal ones for first outreach.
_ROLE_LOCAL_PARTS = (
    "sales",
    "info",
    "contact",
    "export",
    "trade",
    "trading",
    "enquiries",
    "inquiries",
    "office",
    "admin",
    "hello",
)


class SiteCrawler:
    def __init__(
        self,
        timeout: float = 8.0,
        max_pages: int = 3,
        max_bytes: int = 300_000,
    ) -> None:
        self._timeout = timeout
        self._max_pages = max_pages
        self._max_bytes = max_bytes

    async def enrich(self, website: str | None) -> dict:
        """Return best-effort {email, phone} for a supplier's website.

        Missing keys are omitted. Never raises — a crawl failure just
        returns {} so callers can continue with whatever they have.
        """
        if not website:
            return {}
        base = _normalize_base(website)
        if not base:
            return {}

        emails: list[str] = []
        phones: list[str] = []
        host = urlparse(base).netloc.lower().removeprefix("www.")

        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "AtlasTradeBot/1.0 (+contact-discovery)"},
        ) as client:
            for path in _CONTACT_PATHS[: self._max_pages]:
                url = urljoin(base, path)
                try:
                    resp = await client.get(url)
                except httpx.HTTPError as exc:
                    logger.debug("crawl failed for %s: %s", url, exc)
                    continue
                if resp.status_code >= 400 or not resp.text:
                    continue
                html = resp.text[: self._max_bytes]
                emails.extend(_extract_emails(html, host))
                phones.extend(_extract_phones(html))
                if emails:
                    break

        out: dict = {}
        if emails:
            out["email"] = _pick_best_email(emails, host)
        if phones:
            out["phone"] = phones[0]
        return out


def _normalize_base(url: str) -> str | None:
    u = url.strip()
    if not u:
        return None
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    parsed = urlparse(u)
    if not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _extract_emails(html: str, host: str) -> list[str]:
    found = set(m.group(1).lower() for m in _MAILTO_RE.finditer(html))
    text = _HTML_TAG_RE.sub(" ", html)
    for m in _EMAIL_RE.finditer(text):
        found.add(m.group(0).lower())
    return [
        e
        for e in found
        if _looks_real(e) and not e.endswith((".png", ".jpg", ".gif", ".svg"))
    ]


def _extract_phones(html: str) -> list[str]:
    text = _WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", html))
    return [m.group(0).strip() for m in _PHONE_RE.finditer(text)][:5]


def _looks_real(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    if local in _GENERIC_LOCAL_PARTS:
        return False
    # Avoid assets like sprite-logos@2x.png that slip past the extension filter.
    if any(ch in local for ch in ("+",)) and not any(c.isalpha() for c in local):
        return False
    return True


def _pick_best_email(emails: list[str], host: str) -> str:
    """Prefer emails whose domain matches the supplier's site, and whose
    local part is a role address (sales@, info@…) over personal addresses.
    """

    def score(e: str) -> tuple[int, int, int]:
        local, _, domain = e.partition("@")
        domain = domain.lower()
        local = local.lower()
        domain_match = 1 if domain == host or domain.endswith("." + host) else 0
        role_rank = next(
            (i for i, r in enumerate(_ROLE_LOCAL_PARTS) if local.startswith(r)),
            len(_ROLE_LOCAL_PARTS),
        )
        # Higher domain match wins; lower role_rank (more generic) wins within match.
        return (domain_match, -role_rank, -len(local))

    return sorted(set(emails), key=score, reverse=True)[0]
