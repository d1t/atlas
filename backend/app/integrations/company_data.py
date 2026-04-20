"""Company enrichment integration stub.

Plug in Clearbit / OpenCorporates / LinkedIn-via-Apify by implementing `enrich`.
"""
from __future__ import annotations


class CompanyDataProvider:
    async def enrich(self, name: str, website: str | None = None) -> dict:
        # TODO: wire real provider. Return an empty enrichment for now.
        return {
            "name": name,
            "website": website,
            "employees": None,
            "revenue": None,
            "registered": None,
            "source": "mock",
        }
