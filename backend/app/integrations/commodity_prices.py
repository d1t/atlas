"""Commodity pricing integration stub.

Plug in providers like Commodities API, Twelve Data, or an S&P Global feed
by implementing `get_price`.
"""
from __future__ import annotations


class CommodityPricesProvider:
    async def get_price(self, commodity: str, currency: str = "USD") -> dict:
        # TODO: wire a real provider via HTTP. For now return a flat mock.
        return {
            "commodity": commodity,
            "currency": currency,
            "unit": "MT",
            "price": 0.0,
            "source": "mock",
        }
