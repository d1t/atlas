"""Third-party integration adapters.

Each integration exposes an async interface. The PRD mandates a pluggable
provider pattern — live keys are optional.
"""
from app.integrations.commodity_prices import CommodityPricesProvider
from app.integrations.company_data import CompanyDataProvider

__all__ = ["CommodityPricesProvider", "CompanyDataProvider"]
