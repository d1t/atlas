"""Commodity price endpoints backed by Yahoo Finance."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from app.integrations.yahoo_finance import COMMODITIES, get_price, resolve_commodity

router = APIRouter()


@router.get("")
async def list_commodities() -> dict:
    """List commodities Atlas knows how to price."""
    return {
        "commodities": [
            {
                "slug": spec.slug,
                "display": spec.display,
                "ticker": spec.ticker,
                "exchange": spec.exchange,
                "quoted_unit": spec.quoted_unit,
                "supports_mt": spec.mt_multiplier is not None,
            }
            for spec in COMMODITIES.values()
        ]
    }


@router.get("/{commodity}")
async def get_commodity_price(
    commodity: str,
    refresh: bool = Query(False, description="Bypass 5-minute cache"),
) -> dict:
    """Return the latest futures price for ``commodity``.

    ``commodity`` can be a slug (``sugar``, ``wheat``, ...) or free-text that
    matches one of the configured aliases (e.g. ``raw sugar``, ``icumsa 45``).
    """
    spec = resolve_commodity(commodity)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail=f"No price feed configured for commodity '{commodity}'",
        )

    if refresh:
        # Best-effort invalidate — avoids another import cycle by poking
        # the private cache directly.
        from app.integrations.yahoo_finance import _cache  # noqa: PLC0415

        _cache.pop(spec.ticker, None)

    quote = await get_price(commodity)
    if quote is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Yahoo Finance did not return a price for {spec.ticker}. "
                "Try again in a moment — upstream may be rate-limiting or down."
            ),
        )
    return asdict(quote)
