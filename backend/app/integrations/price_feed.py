"""Commodity futures price feed.

We fetch per-commodity futures quotes and convert native exchange units into
USD/MT so the pricing engine can use a single unit. No API key required.

Sources are tried in order:

1. **CNBC** (``quote.cnbc.com``) — primary. A public JSON quote service that
   serves datacenter IPs without throttling.
2. **Yahoo Finance** — fallback. Its chart endpoint blocks cloud egress with a
   blanket HTTP 429 (including the cookie/crumb handshake endpoint, so the
   handshake cannot bootstrap from a blocked host), which makes it unusable as
   a primary source from a server. It still works from residential IPs, so it
   is kept behind a circuit breaker rather than dropped.

Stooq was previously the fallback but its CSV endpoint (``/q/l/``) now returns
404 for every symbol, so it has been removed.

Falls back to ``None`` on any HTTP or parse error — the UI renders
"unavailable" rather than the deal engine breaking.

Unit conversions (sources: ICE, CBOT, CME contract specs):
- Sugar #11 (SB=F): quoted in US cents / lb. 1 MT = 2204.62 lb.
    price_usd_per_mt = (cents_per_lb / 100) * 2204.62
- Wheat (ZW=F), Corn (ZC=F), Soybeans (ZS=F): quoted in US cents / bushel.
  Bushel→MT conversion depends on the commodity (different weights per bushel):
    Wheat/Soy:   1 MT = 36.7437 bushels  (60 lb/bushel)
    Corn:        1 MT = 39.3680 bushels  (56 lb/bushel)
    price_usd_per_mt = (cents_per_bushel / 100) * bushels_per_mt
- Coffee C (KC=F): US cents / lb.                → same as sugar
- Cocoa (CC=F): USD / MT (already correct).      → no conversion
- Cotton #2 (CT=F): US cents / lb.               → same as sugar
- Crude Oil WTI (CL=F): USD / barrel.            → kept as $/bbl (not $/MT)
- Gold (GC=F): USD / troy oz.                    → kept as $/oz

CNBC quotes the same contracts in the same units, so one conversion table
serves both sources.

Anything not in the map returns raw quote + "unit unknown".
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, replace
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_CNBC_QUOTE_URL = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
)
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
# Yahoo's cookie+crumb handshake. Visiting fc.yahoo.com sets the consent
# cookie; getcrumb then returns a short token that must accompany data calls.
# Note the crumb endpoint sits on the same host Yahoo blocks, so on a blocked
# network the handshake cannot bootstrap and we proceed anonymously.
_YAHOO_COOKIE_URL = "https://fc.yahoo.com/"
_YAHOO_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# --- Retry / backoff -------------------------------------------------------
# Upstreams occasionally answer transient 429/5xx or time out. Retry those a
# few times with exponential backoff + jitter before giving up / falling back.
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.4  # seconds; grows 0.4, 0.8, ... plus jitter
_RETRY_JITTER = 0.3
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _backoff_delay(attempt: int) -> float:
    return _RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, _RETRY_JITTER)


@dataclass(frozen=True)
class CommoditySpec:
    """Static mapping of a commodity slug to its futures symbols + units."""

    slug: str
    display: str
    ticker: str
    exchange: str
    # Quoted unit and numeric conversion to $/MT. None = not applicable.
    quoted_unit: str  # e.g. "cents/lb", "cents/bushel", "USD/MT"
    # Multiplier applied to the *raw* quote to produce USD/MT.
    # If None, we do not convert and report the native unit.
    mt_multiplier: float | None
    # CNBC front-month futures symbol (``@`` prefix, ``.1`` = front month).
    cnbc_symbol: str | None = None


# Keep this tight — adding is easy; each entry should be verified against
# the exchange contract spec.
COMMODITIES: dict[str, CommoditySpec] = {
    "sugar": CommoditySpec(
        slug="sugar",
        display="Sugar #11",
        ticker="SB=F",
        exchange="ICE",
        quoted_unit="cents/lb",
        mt_multiplier=22.0462,  # (cents/lb ÷ 100) × 2204.62 lb/MT
        cnbc_symbol="@SB.1",
    ),
    "wheat": CommoditySpec(
        slug="wheat",
        display="Wheat",
        ticker="ZW=F",
        exchange="CBOT",
        quoted_unit="cents/bushel",
        mt_multiplier=0.367437,  # (cents/bu ÷ 100) × 36.7437 bu/MT
        cnbc_symbol="@W.1",
    ),
    "corn": CommoditySpec(
        slug="corn",
        display="Corn",
        ticker="ZC=F",
        exchange="CBOT",
        quoted_unit="cents/bushel",
        mt_multiplier=0.393680,  # (cents/bu ÷ 100) × 39.3680 bu/MT
        cnbc_symbol="@C.1",
    ),
    "soybeans": CommoditySpec(
        slug="soybeans",
        display="Soybeans",
        ticker="ZS=F",
        exchange="CBOT",
        quoted_unit="cents/bushel",
        mt_multiplier=0.367437,
        cnbc_symbol="@S.1",
    ),
    "coffee": CommoditySpec(
        slug="coffee",
        display="Coffee C",
        ticker="KC=F",
        exchange="ICE",
        quoted_unit="cents/lb",
        mt_multiplier=22.0462,
        cnbc_symbol="@KC.1",
    ),
    "cocoa": CommoditySpec(
        slug="cocoa",
        display="Cocoa",
        ticker="CC=F",
        exchange="ICE",
        quoted_unit="USD/MT",
        mt_multiplier=1.0,
        cnbc_symbol="@CC.1",
    ),
    "cotton": CommoditySpec(
        slug="cotton",
        display="Cotton #2",
        ticker="CT=F",
        exchange="ICE",
        quoted_unit="cents/lb",
        mt_multiplier=22.0462,
        cnbc_symbol="@CT.1",
    ),
    "crude_oil": CommoditySpec(
        slug="crude_oil",
        display="WTI Crude",
        ticker="CL=F",
        exchange="NYMEX",
        quoted_unit="USD/bbl",
        mt_multiplier=None,  # report $/bbl, not $/MT
        cnbc_symbol="@CL.1",
    ),
    "gold": CommoditySpec(
        slug="gold",
        display="Gold",
        ticker="GC=F",
        exchange="COMEX",
        quoted_unit="USD/oz",
        mt_multiplier=None,
        cnbc_symbol="@GC.1",
    ),
}


def resolve_commodity(name: str | None) -> CommoditySpec | None:
    """Map a free-text commodity string to a spec, if we know about it.

    Matches against slug, display name, and a few common aliases. Lowercased
    substring match to handle things like "Brazilian raw sugar" → sugar.
    """
    if not name:
        return None
    n = name.lower().strip()
    aliases = {
        "sugar": "sugar",
        "brown sugar": "sugar",
        "raw sugar": "sugar",
        "icumsa": "sugar",
        "wheat": "wheat",
        "corn": "corn",
        "maize": "corn",
        "soybean": "soybeans",
        "soybeans": "soybeans",
        "soya": "soybeans",
        "coffee": "coffee",
        "arabica": "coffee",
        "cocoa": "cocoa",
        "cacao": "cocoa",
        "cotton": "cotton",
        "crude": "crude_oil",
        "oil": "crude_oil",
        "wti": "crude_oil",
        "gold": "gold",
    }
    for key, slug in aliases.items():
        if key in n:
            return COMMODITIES[slug]
    return COMMODITIES.get(n)


@dataclass
class PriceQuote:
    commodity: str
    display: str
    ticker: str
    exchange: str
    quoted_unit: str
    raw_price: float
    price_mt: float | None  # USD/MT, or None if conversion N/A
    currency: str
    timestamp: int  # unix seconds
    previous_close: float | None
    change_pct: float | None
    source: str = "yahoo_finance"
    # When True, this was served from the in-memory cache.
    stale: bool = False


# ---------------- In-memory TTL cache (per process) ----------------

# Fresh window: within this a cached quote is served directly. Widened from the
# original 5 min to cut upstream calls (and thus rate-limit exposure).
_CACHE_TTL_SECONDS = 900  # 15 minutes
_cache: dict[str, tuple[float, PriceQuote]] = {}
# Last successful quote per ticker, kept regardless of age. Used to serve a
# stale price (flagged ``stale=True``) when a live fetch fails, so the UI shows
# a slightly old number instead of an error.
_last_good: dict[str, PriceQuote] = {}
_locks: dict[str, asyncio.Lock] = {}

# Cached (fetched_at, crumb, cookies) for the Yahoo handshake.
_CRUMB_TTL_SECONDS = 3600  # 1 hour
_crumb_cache: tuple[float, str, dict[str, str]] | None = None
# Cloud/datacenter egress is often blocked outright, in which case the
# handshake 429s too. Remember that so every commodity in a page load doesn't
# repeat it — otherwise one dashboard render triggers a burst of doomed calls
# and deepens the rate limit.
_CRUMB_FAILURE_TTL_SECONDS = 600  # 10 minutes
_crumb_failed_at: float | None = None

# Circuit breaker for the Yahoo data host. Yahoo does not throttle gradually
# from datacenter IPs — it blocks. Once retries are exhausted, skip Yahoo for a
# cooldown and go straight to the fallback, so a blocked host costs one attempt
# per cooldown rather than three per commodity per request.
_YAHOO_COOLDOWN_SECONDS = 300  # 5 minutes
_yahoo_blocked_until: float = 0.0


def _yahoo_in_cooldown() -> bool:
    return time.time() < _yahoo_blocked_until


def _trip_yahoo_breaker() -> None:
    global _yahoo_blocked_until
    _yahoo_blocked_until = time.time() + _YAHOO_COOLDOWN_SECONDS
    logger.warning(
        "Yahoo appears to be blocking this host; skipping it for %ds",
        _YAHOO_COOLDOWN_SECONDS,
    )


def _get_lock(ticker: str) -> asyncio.Lock:
    lock = _locks.get(ticker)
    if lock is None:
        lock = asyncio.Lock()
        _locks[ticker] = lock
    return lock


async def get_price(commodity: str) -> PriceQuote | None:
    """Fetch the latest quote for ``commodity`` (slug or free-text name).

    Uses a 5-minute cache. Returns ``None`` if the commodity is unknown or
    the upstream request fails.
    """
    spec = resolve_commodity(commodity)
    if spec is None:
        logger.debug("Unknown commodity for price lookup: %r", commodity)
        return None

    now = time.time()
    cached = _cache.get(spec.ticker)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    async with _get_lock(spec.ticker):
        # Re-check after acquiring the lock — another caller may have filled.
        cached = _cache.get(spec.ticker)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

        quote = await _fetch_live(spec)
        if quote is not None:
            _cache[spec.ticker] = (time.time(), quote)
            _last_good[spec.ticker] = quote
            return quote

        # Live fetch failed — serve the last good quote (if any) as stale
        # rather than nothing, so traders see a recent-ish reference price.
        stale = _last_good.get(spec.ticker)
        if stale is not None:
            logger.warning(
                "Serving stale %s quote after fetch failure", spec.ticker
            )
            return replace(stale, stale=True)
        return None


async def _get_crumb() -> tuple[str | None, dict[str, str]]:
    """Perform (and cache) Yahoo's cookie+crumb handshake.

    Returns ``(crumb, cookies)``. On any failure returns ``(None, {})`` so the
    caller falls back to an anonymous request rather than breaking.
    """
    global _crumb_cache, _crumb_failed_at
    now = time.time()
    if _crumb_cache is not None and (now - _crumb_cache[0]) < _CRUMB_TTL_SECONDS:
        return _crumb_cache[1], _crumb_cache[2]
    if (
        _crumb_failed_at is not None
        and (now - _crumb_failed_at) < _CRUMB_FAILURE_TTL_SECONDS
    ):
        return None, {}

    headers = {"User-Agent": _USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            # Seed consent cookies; this endpoint often 404s but still sets them.
            try:
                await client.get(_YAHOO_COOKIE_URL)
            except httpx.HTTPError:
                pass
            resp = await client.get(_YAHOO_CRUMB_URL)
            resp.raise_for_status()
            crumb = resp.text.strip()
            cookies = {c.name: c.value for c in client.cookies.jar}
    except httpx.HTTPError as exc:
        logger.info("Yahoo crumb handshake failed (continuing anonymously): %s", exc)
        _crumb_failed_at = now
        return None, {}

    # A valid crumb is a short opaque token; HTML means we got an error page.
    if not crumb or "<" in crumb or len(crumb) > 64:
        logger.info("Yahoo crumb response looked invalid; continuing anonymously")
        _crumb_failed_at = now
        return None, {}

    _crumb_cache = (now, crumb, cookies)
    _crumb_failed_at = None
    return crumb, cookies


async def _get_with_retry(
    url: str,
    *,
    params: dict[str, Any],
    headers: dict[str, str],
    cookies: dict[str, str] | None = None,
) -> httpx.Response | None:
    """GET ``url`` retrying transient 429/5xx + timeouts with backoff+jitter.

    Returns the ``Response`` on success (any non-retryable status) or ``None``
    if it exhausted retries or hit a non-retryable transport error.
    """
    last: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(
                timeout=10.0, cookies=cookies or None
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            last = exc
        except httpx.HTTPError as exc:
            # Connect/DNS errors etc. — not worth retrying; let caller fall back.
            logger.warning("HTTP error for %s: %s", url, exc)
            return None
        else:
            if resp.status_code not in _RETRYABLE_STATUS:
                return resp
            last = httpx.HTTPStatusError(
                f"retryable status {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        if attempt < _MAX_ATTEMPTS - 1:
            await asyncio.sleep(_backoff_delay(attempt))
    logger.warning("Exhausted retries for %s (%s)", url, last)
    return None


async def _fetch_live(spec: CommoditySpec) -> PriceQuote | None:
    """Try each source in order, returning the first usable quote."""
    if spec.cnbc_symbol:
        quote = await _fetch_cnbc(spec)
        if quote is not None:
            return quote
    return await _fetch_yahoo_chart(spec)


async def _fetch_cnbc(spec: CommoditySpec) -> PriceQuote | None:
    """Primary source — CNBC's public quote service."""
    assert spec.cnbc_symbol is not None
    params: dict[str, Any] = {
        "symbols": spec.cnbc_symbol,
        "requestMethod": "itv",
        "noform": "1",
        "partnerId": "2",
        "fund": "1",
        "exthrs": "1",
        "output": "json",
        "events": "1",
    }
    resp = await _get_with_retry(
        _CNBC_QUOTE_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    if resp is None:
        return None
    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("CNBC returned non-JSON for %s: %s", spec.cnbc_symbol, exc)
        return None

    return _parse_cnbc(data, spec)


def _parse_cnbc(data: dict[str, Any], spec: CommoditySpec) -> PriceQuote | None:
    """Pull the last trade out of CNBC's ``FormattedQuoteResult`` envelope.

    A single-symbol request may return the quote as an object rather than a
    one-element list, so both shapes are handled.
    """
    try:
        quotes = data["FormattedQuoteResult"]["FormattedQuote"]
    except (KeyError, TypeError):
        logger.warning("Unexpected CNBC payload shape for %s", spec.cnbc_symbol)
        return None
    if isinstance(quotes, dict):
        quotes = [quotes]
    if not quotes:
        return None
    q = quotes[0]
    if not isinstance(q, dict):
        return None

    raw = _safe_float(str(q.get("last", "")))
    if raw is None:
        return None
    # "UNCH" (unchanged) and blanks are common in the previous-close field.
    prev = _safe_float(str(q.get("previous_day_closing", "")))

    change_pct = None
    if prev and prev > 0:
        change_pct = round((raw - prev) / prev * 100, 2)

    price_mt = (
        round(raw * spec.mt_multiplier, 2) if spec.mt_multiplier is not None else None
    )

    return PriceQuote(
        commodity=spec.slug,
        display=spec.display,
        ticker=spec.ticker,
        exchange=spec.exchange,
        quoted_unit=spec.quoted_unit,
        raw_price=round(raw, 4),
        price_mt=price_mt,
        currency=q.get("currencyCode") or "USD",
        timestamp=int(time.time()),
        previous_close=round(prev, 4) if prev is not None else None,
        change_pct=change_pct,
        source="cnbc",
    )


async def _fetch_yahoo_chart(spec: CommoditySpec) -> PriceQuote | None:
    if _yahoo_in_cooldown():
        return None

    url = _YAHOO_CHART_URL.format(ticker=spec.ticker)
    params: dict[str, Any] = {"interval": "1d", "range": "5d"}
    crumb, cookies = await _get_crumb()
    if crumb:
        params["crumb"] = crumb

    resp = await _get_with_retry(
        url,
        params=params,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        cookies=cookies,
    )
    if resp is None:
        _trip_yahoo_breaker()
        return None
    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("Yahoo Finance returned non-JSON for %s: %s", spec.ticker, exc)
        return None

    return _parse_chart(data, spec)


def _safe_float(x: str) -> float | None:
    """Parse an upstream numeric string, tolerating thousands separators and
    the various placeholders feeds use for "no value" (CNBC sends ``UNCH`` for
    an unchanged/absent field).
    """
    x = x.strip().replace(",", "")
    if not x or x.upper() in {"N/D", "N/A", "NA", "UNCH", "NONE"}:
        return None
    try:
        return float(x)
    except ValueError:
        return None


def _parse_chart(data: dict[str, Any], spec: CommoditySpec) -> PriceQuote | None:
    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        raw = meta.get("regularMarketPrice")
        if raw is None:
            return None
        raw = float(raw)
        prev = meta.get("chartPreviousClose")
        prev_f = float(prev) if prev is not None else None
        ts = int(meta.get("regularMarketTime") or time.time())
        currency = meta.get("currency") or "USD"
    except (KeyError, IndexError, TypeError, ValueError):
        logger.warning("Unexpected Yahoo payload shape for %s", spec.ticker)
        return None

    price_mt = None
    if spec.mt_multiplier is not None:
        price_mt = round(raw * spec.mt_multiplier, 2)

    change_pct = None
    if prev_f and prev_f > 0:
        change_pct = round((raw - prev_f) / prev_f * 100, 2)

    return PriceQuote(
        commodity=spec.slug,
        display=spec.display,
        ticker=spec.ticker,
        exchange=spec.exchange,
        quoted_unit=spec.quoted_unit,
        raw_price=round(raw, 4),
        price_mt=price_mt,
        currency=currency,
        timestamp=ts,
        previous_close=round(prev_f, 4) if prev_f is not None else None,
        change_pct=change_pct,
    )


def clear_cache() -> None:
    """Test hook."""
    global _crumb_cache, _crumb_failed_at, _yahoo_blocked_until
    _cache.clear()
    _last_good.clear()
    _locks.clear()
    _crumb_cache = None
    _crumb_failed_at = None
    _yahoo_blocked_until = 0.0
