"""Yahoo Finance price feed.

Yahoo's public chart endpoint is undocumented but stable enough for an MVP
reference price. We fetch per-commodity futures tickers and convert native
exchange units into USD/MT so the pricing engine can use a single unit.

No API key required. Falls back to ``None`` on any HTTP or parse error — the
UI renders "unavailable" rather than the deal engine breaking.

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

Anything not in the map returns raw quote + "unit unknown".
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@dataclass(frozen=True)
class CommoditySpec:
    """Static mapping of a commodity slug to its Yahoo ticker + units."""

    slug: str
    display: str
    ticker: str
    exchange: str
    # Quoted unit and numeric conversion to $/MT. None = not applicable.
    quoted_unit: str  # e.g. "cents/lb", "cents/bushel", "USD/MT"
    # Multiplier applied to the *raw* Yahoo quote to produce USD/MT.
    # If None, we do not convert and report the native unit.
    mt_multiplier: float | None
    # Stooq symbol — fallback if Yahoo is rate-limiting (datacenter IPs often
    # get 429). Stooq uses lower-case ``.f`` suffix for front-month futures.
    stooq_symbol: str | None = None


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
        stooq_symbol="sb.f",
    ),
    "wheat": CommoditySpec(
        slug="wheat",
        display="Wheat",
        ticker="ZW=F",
        exchange="CBOT",
        quoted_unit="cents/bushel",
        mt_multiplier=0.367437,  # (cents/bu ÷ 100) × 36.7437 bu/MT
        stooq_symbol="zw.f",
    ),
    "corn": CommoditySpec(
        slug="corn",
        display="Corn",
        ticker="ZC=F",
        exchange="CBOT",
        quoted_unit="cents/bushel",
        mt_multiplier=0.393680,  # (cents/bu ÷ 100) × 39.3680 bu/MT
        stooq_symbol="zc.f",
    ),
    "soybeans": CommoditySpec(
        slug="soybeans",
        display="Soybeans",
        ticker="ZS=F",
        exchange="CBOT",
        quoted_unit="cents/bushel",
        mt_multiplier=0.367437,
        stooq_symbol="zs.f",
    ),
    "coffee": CommoditySpec(
        slug="coffee",
        display="Coffee C",
        ticker="KC=F",
        exchange="ICE",
        quoted_unit="cents/lb",
        mt_multiplier=22.0462,
        stooq_symbol="kc.f",
    ),
    "cocoa": CommoditySpec(
        slug="cocoa",
        display="Cocoa",
        ticker="CC=F",
        exchange="ICE",
        quoted_unit="USD/MT",
        mt_multiplier=1.0,
        stooq_symbol="cc.f",
    ),
    "cotton": CommoditySpec(
        slug="cotton",
        display="Cotton #2",
        ticker="CT=F",
        exchange="ICE",
        quoted_unit="cents/lb",
        mt_multiplier=22.0462,
        stooq_symbol="ct.f",
    ),
    "crude_oil": CommoditySpec(
        slug="crude_oil",
        display="WTI Crude",
        ticker="CL=F",
        exchange="NYMEX",
        quoted_unit="USD/bbl",
        mt_multiplier=None,  # report $/bbl, not $/MT
        stooq_symbol="cl.f",
    ),
    "gold": CommoditySpec(
        slug="gold",
        display="Gold",
        ticker="GC=F",
        exchange="COMEX",
        quoted_unit="USD/oz",
        mt_multiplier=None,
        stooq_symbol="gc.f",
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

_CACHE_TTL_SECONDS = 300  # 5 minutes per PRD
_cache: dict[str, tuple[float, PriceQuote]] = {}
_locks: dict[str, asyncio.Lock] = {}


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

        quote = await _fetch_yahoo(spec)
        if quote is not None:
            _cache[spec.ticker] = (time.time(), quote)
        return quote


async def _fetch_yahoo(spec: CommoditySpec) -> PriceQuote | None:
    """Try Yahoo Finance first, fall back to stooq.com on any error.

    Yahoo's chart endpoint rate-limits datacenter IPs aggressively (HTTP 429),
    so production deployments behind cloud egress often fail. Stooq offers a
    simpler public CSV feed and makes a reliable second source.
    """
    quote = await _fetch_yahoo_chart(spec)
    if quote is not None:
        return quote
    if spec.stooq_symbol:
        return await _fetch_stooq(spec)
    return None


async def _fetch_yahoo_chart(spec: CommoditySpec) -> PriceQuote | None:
    url = _YAHOO_CHART_URL.format(ticker=spec.ticker)
    params = {"interval": "1d", "range": "5d"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Yahoo Finance fetch failed for %s: %s", spec.ticker, exc)
        return None

    return _parse_chart(data, spec)


async def _fetch_stooq(spec: CommoditySpec) -> PriceQuote | None:
    """Fallback source — stooq.com end-of-day / intraday CSV."""
    assert spec.stooq_symbol is not None
    url = "https://stooq.com/q/l/"
    params = {"s": spec.stooq_symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/csv,*/*"},
            )
            resp.raise_for_status()
            body = resp.text
    except httpx.HTTPError as exc:
        logger.warning("Stooq fetch failed for %s: %s", spec.stooq_symbol, exc)
        return None

    return _parse_stooq_csv(body, spec)


def _parse_stooq_csv(body: str, spec: CommoditySpec) -> PriceQuote | None:
    # Expected shape: two lines. Header + one data row. Close is col 6.
    # "Symbol,Date,Time,Open,High,Low,Close,Volume\nSB.F,2026-04-20,12:44:56,13.57,13.61,13.43,13.44,"
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    cols = lines[1].split(",")
    if len(cols) < 7:
        return None
    try:
        open_ = _safe_float(cols[3])
        close = _safe_float(cols[6])
        if close is None:
            return None
        raw = close
    except (IndexError, ValueError):
        return None

    # Stooq returns N/D for missing opens — in that case we can't compute change.
    prev = open_  # best-effort "previous" ref: today's open (not yesterday's close)
    change_pct = None
    if prev and prev > 0:
        change_pct = round((raw - prev) / prev * 100, 2)

    # Stooq's date/time columns are UTC-ish but parsing is overkill; use now().
    ts = int(time.time())

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
        currency="USD",
        timestamp=ts,
        previous_close=round(prev, 4) if prev is not None else None,
        change_pct=change_pct,
        source="stooq",
    )


def _safe_float(x: str) -> float | None:
    x = x.strip()
    if not x or x.upper() in {"N/D", "N/A", "NA"}:
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
    _cache.clear()
    _locks.clear()
