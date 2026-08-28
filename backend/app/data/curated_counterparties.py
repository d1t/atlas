"""Curated counterparty registry for narrow, well-known trade lanes.

For some commodity/origin pairs the universe of credible counterparties is
small and stable — Brazil raw sugar is a canonical example: ~5 desks move the
overwhelming majority of physical volume. Running an email-enrichment vendor
on top of broad web search is overkill (and noisy) for those lanes; a vetted
seed list is faster, more accurate, and avoids guessed addresses.

Entries are intentionally *minimal*: name, country, role, website, and a short
note. Emails are deliberately left empty — the existing site-crawler
(``app.scrapers.site_crawler``) extracts contact addresses from each
counterparty's own ``/contact`` page on first AI-classify, which avoids
shipping guessed role inboxes that may bounce.

Lookup is keyed by ``(commodity, country)`` normalised to lowercase. Match is
exact on commodity and exact on country; if you need fuzzier matching add it
at the call site.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedCounterparty:
    """A pre-vetted counterparty for a specific commodity/origin lane.

    Attributes:
        name: Legal / commercial name.
        country: Country of operation (matches Opportunity.destination_country
            on the supply side, i.e. where the supplier is located).
        commodity: Commodity this counterparty trades (raw sugar, white sugar,
            etc.). Matched case-insensitively against ``Opportunity.commodity``.
        website: Public website. Used by the contact-crawler to find emails.
        type: Free-form role label ("merchant trader", "producer co-op",
            "trading house"), used for the supplier classification badge.
        description: One-sentence positioning that surfaces in the UI so the
            user knows *why* this counterparty is on the curated list.
    """

    name: str
    country: str
    commodity: str
    website: str
    type: str
    description: str


# Brazil raw sugar — Copersucar, Alvean (now Copersucar-controlled post-2023
# JV restructure), Raízen, Sucden Brazil, Czarnikow Brazil. Together these
# desks intermediate the bulk of Brazilian raw-sugar export flow.
_REGISTRY: list[CuratedCounterparty] = [
    CuratedCounterparty(
        name="Copersucar S.A.",
        country="Brazil",
        commodity="sugar",
        website="https://www.copersucar.com.br",
        type="producer co-op / trader",
        description=(
            "São Paulo-based co-operative aggregating ~30 Brazilian mills; "
            "consistently among the top global raw-sugar exporters by volume."
        ),
    ),
    CuratedCounterparty(
        name="Alvean",
        country="Brazil",
        commodity="sugar",
        website="https://www.alvean.com",
        type="trading house",
        description=(
            "Founded 2014 as a Cargill / Copersucar JV; following the 2023 "
            "restructure Cargill exited and Alvean now operates within the "
            "Copersucar group. Worth including for relationship continuity, "
            "but a contact reached here may overlap with Copersucar's desk."
        ),
    ),
    CuratedCounterparty(
        name="Raízen",
        country="Brazil",
        commodity="sugar",
        website="https://www.raizen.com.br",
        type="producer / trader",
        description=(
            "Cosan + Shell JV; one of the largest individual sugarcane "
            "crushers globally with an integrated São Paulo sugar trading desk."
        ),
    ),
    CuratedCounterparty(
        name="Sucden Brazil",
        country="Brazil",
        commodity="sugar",
        website="https://www.sucden.com",
        type="merchant trader",
        description=(
            "São Paulo desk of Sucres et Denrées (Paris); long-standing "
            "Brazilian raw-sugar origination presence."
        ),
    ),
    CuratedCounterparty(
        name="Czarnikow Brazil",
        country="Brazil",
        commodity="sugar",
        website="https://www.czarnikow.com",
        type="merchant trader / broker",
        description=(
            "São Paulo desk of the Czarnikow Group; broker-trader with deep "
            "ties to Brazilian mill production."
        ),
    ),
]


def _normalise(value: str | None) -> str:
    return (value or "").strip().casefold()


def get_curated_counterparties(
    commodity: str | None, country: str | None = None
) -> list[CuratedCounterparty]:
    """Return curated counterparties whose commodity matches the request.

    ``commodity`` is matched as a case-insensitive substring so that
    ``"raw sugar"``, ``"sugar (ICUMSA 45)"`` and ``"White Sugar"`` all hit the
    ``"sugar"`` entries.

    ``country`` is optional and only used as an additional filter when caller
    wants to scope to a specific origin (e.g. "Brazil sugar only"). The
    primary lookup key for the curated panel is *commodity* — origin is shown
    to the user as part of each entry rather than required up-front, because
    the ``Opportunity`` model only carries a destination country.
    """
    needle_commodity = _normalise(commodity)
    if not needle_commodity:
        return []
    needle_country = _normalise(country) if country else None
    return [
        cp
        for cp in _REGISTRY
        if _normalise(cp.commodity) in needle_commodity
        and (needle_country is None or _normalise(cp.country) == needle_country)
    ]
