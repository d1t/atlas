"""DuckDuckGo HTML search integration.

DuckDuckGo does not offer an official JSON search API, but their
legacy HTML endpoint (`https://html.duckduckgo.com/html/`) is public,
unauthenticated, and returns real organic results. We parse the HTML
with regex rather than pulling in BeautifulSoup since the markup is
stable and the surface area we need is tiny.

Caveats:
- DuckDuckGo rate-limits aggressively. On 202/403/429 we return [] and
  fall back to whatever other providers produced.
- Result URLs are wrapped in a DDG click-through redirect
  (`/l/?uddg=<encoded>`). We unwrap before returning.
- This is a best-effort secondary source. If DDG changes its HTML we
  degrade silently to Tavily-only.

No API key required. No signup. Nothing to configure when enabled.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"

# Each organic result on the HTML endpoint looks roughly like:
#   <a class="result__a" href="//duckduckgo.com/l/?uddg=...">Title</a>
#   <a class="result__snippet" href="...">Snippet text</a>
# We don't rely on structure — just anchor on the two CSS classes.
_RESULT_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class DuckDuckGoSearch:
    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = bool(settings.ddg_search_enabled)
        self._max_results = settings.ddg_max_results

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def search_suppliers(
        self, commodity: str, country: str | None = None
    ) -> list[dict]:
        """Return SERP hits shaped like the Tavily adapter for pipeline parity."""
        if not self._enabled:
            return []

        geo = f" in {country}" if country else ""
        query = (
            f"{commodity} supplier exporter mill refinery producer{geo} "
            f"contact email website"
        )

        headers = {
            # DDG blocks obvious bots. Use a common browser UA.
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True
            ) as client:
                resp = await client.post(
                    _DDG_URL, data={"q": query}, headers=headers
                )
        except httpx.HTTPError as exc:
            logger.warning("DDG search failed: %s", exc)
            return []

        # DDG returns 202 as a soft rate-limit signal (served but with a
        # captcha/interstitial body), plus the usual 4xx/5xx. Treat all
        # of them as "drop silently, let other providers cover us".
        if resp.status_code >= 400 or resp.status_code == 202 or not resp.text:
            logger.info("DDG returned status %s", resp.status_code)
            return []

        hits: list[dict] = []
        for m in _RESULT_RE.finditer(resp.text):
            raw_href = m.group(1)
            title_html = m.group(2)
            snippet_html = m.group(3)
            url = _unwrap_ddg(raw_href)
            if not url:
                continue
            hits.append(
                {
                    "url": url,
                    "title": _clean_html(title_html),
                    "snippet": _clean_html(snippet_html),
                    "score": None,
                    "source": "duckduckgo",
                }
            )
            if len(hits) >= self._max_results:
                break
        logger.info(
            "DuckDuckGo returned %d hits for %s (%s)", len(hits), commodity, country
        )
        return hits


def _unwrap_ddg(href: str) -> str | None:
    """Strip DDG's click-through redirect to get the real URL."""
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        q = parse_qs(parsed.query)
        uddg = q.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
        return None
    # Some results are already direct links.
    if parsed.scheme in ("http", "https"):
        return href
    return None


def _clean_html(fragment: str) -> str:
    return _WHITESPACE_RE.sub(" ", _TAG_RE.sub("", fragment)).strip()
