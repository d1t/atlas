"""Stage-aware disclosure derivation for opportunity-level facts.

The opportunity row stores the *true* parameters of the deal (exact tonnage,
exact destination port, etc). At early negotiation stages we deliberately
disclose less than the truth to preserve leverage:

- Volume is reduced to a *band* anchored on the low side, so the supplier
  cannot price-discriminate to the buyer's true tonnage.
- Destination is reduced to a *region* (continent / sub-region) until stage
  2, where the country is safe; only at stage 4 is the actual port revealed.
- Single-deal exploration is framed as competitive (multi-origin), which is
  honest leverage in commodity sourcing.

The output of :func:`build_opportunity_disclosure` is injected into the LLM
prompt context as ``opportunity_disclosure``. The prompt rules in
``document_generation.py`` reference fields from this dict by name so that
the model never sees the raw tonnage / port at stage 1.
"""
from __future__ import annotations

from typing import Any

# Mapping from country (lowercased) to a coarse region label safe to use at
# stage 1 in place of the country itself. Falls back to "the destination
# region" if the country is unknown.
_REGION_BY_COUNTRY: dict[str, str] = {
    # West Africa
    "nigeria": "West Africa",
    "ghana": "West Africa",
    "ivory coast": "West Africa",
    "côte d'ivoire": "West Africa",
    "senegal": "West Africa",
    "togo": "West Africa",
    "benin": "West Africa",
    "cameroon": "West Africa",
    # East Africa
    "kenya": "East Africa",
    "tanzania": "East Africa",
    "ethiopia": "East Africa",
    "uganda": "East Africa",
    # North Africa
    "egypt": "North Africa",
    "morocco": "North Africa",
    "algeria": "North Africa",
    "tunisia": "North Africa",
    "libya": "North Africa",
    # Southern Africa
    "south africa": "Southern Africa",
    # Middle East
    "uae": "the Gulf",
    "united arab emirates": "the Gulf",
    "saudi arabia": "the Gulf",
    "qatar": "the Gulf",
    "kuwait": "the Gulf",
    "oman": "the Gulf",
    "bahrain": "the Gulf",
    # Indian subcontinent
    "india": "the Indian subcontinent",
    "pakistan": "the Indian subcontinent",
    "bangladesh": "the Indian subcontinent",
    "sri lanka": "the Indian subcontinent",
    # Southeast Asia
    "indonesia": "Southeast Asia",
    "vietnam": "Southeast Asia",
    "thailand": "Southeast Asia",
    "malaysia": "Southeast Asia",
    "philippines": "Southeast Asia",
    # East Asia
    "china": "East Asia",
    "japan": "East Asia",
    "south korea": "East Asia",
    "taiwan": "East Asia",
    # Europe
    "uk": "Western Europe",
    "united kingdom": "Western Europe",
    "germany": "Western Europe",
    "netherlands": "Western Europe",
    "belgium": "Western Europe",
    "france": "Western Europe",
    "spain": "Western Europe",
    "italy": "Southern Europe",
    "portugal": "Southern Europe",
    "poland": "Eastern Europe",
    "turkey": "the Eastern Mediterranean",
    # Americas
    "brazil": "South America",
    "argentina": "South America",
    "peru": "South America",
    "colombia": "South America",
    "chile": "South America",
    "mexico": "Central America",
    "usa": "North America",
    "united states": "North America",
    "canada": "North America",
}


def region_for(country: str | None) -> str:
    """Return a coarse region label for a country, safe to disclose at stage 1.

    Falls back to a generic phrase when the country is unknown so the LLM
    never receives the raw country at stage 1.
    """
    if not country:
        return "the destination region"
    return _REGION_BY_COUNTRY.get(country.strip().lower(), "the destination region")


def derive_volume_band(volume_mt: float | int | None) -> str | None:
    """Derive a stage-1 disclosure band from the true target tonnage.

    Strategy: anchor the supplier on the LOW end of a wide band so they can't
    price to the user's true willingness-to-buy. The reported volume is the
    supplier-facing figure, never our internal target.

    Bands:

    * `< 5 kMT`     -> "container-scale (single-parcel exploratory)"
    * `5-15 kMT`    -> "small-parcel cargo (5,000-15,000 MT exploratory)"
    * `15-40 kMT`   -> "vessel-scale parcel (15,000-40,000 MT exploratory)"
    * `40-80 kMT`   -> "vessel-scale parcel (25,000-55,000 MT exploratory)"
    * `>= 80 kMT`   -> "multi-vessel programme (50,000-80,000 MT exploratory)"

    Returns ``None`` when the input is missing / non-positive so the caller
    can fall back to "vessel-scale parcel" generically.
    """
    if not isinstance(volume_mt, (int, float)) or volume_mt <= 0:
        return None
    v = float(volume_mt)
    if v < 5_000:
        return "container-scale (single-parcel exploratory)"
    if v < 15_000:
        return "small-parcel cargo (5,000-15,000 MT exploratory)"
    if v < 40_000:
        return "vessel-scale parcel (15,000-40,000 MT exploratory)"
    if v < 80_000:
        return "vessel-scale parcel (25,000-55,000 MT exploratory)"
    return "multi-vessel programme (50,000-80,000 MT exploratory)"


def build_opportunity_disclosure(
    *,
    stage: int,
    volume_mt: float | int | None,
    destination_country: str | None,
    destination_port: str | None,
    commodity: str | None,
) -> dict[str, Any]:
    """Stage-gated disclosure block derived from opportunity facts.

    The returned dict contains ONLY values safe to disclose at the current
    stage. Hidden fields are returned under ``hold`` so the prompt can echo
    them as 'do-not-leak' constraints without ever surfacing the raw values.
    """
    stage = max(1, min(5, int(stage or 1)))
    band = derive_volume_band(volume_mt) or "vessel-scale parcel (exploratory)"
    region = region_for(destination_country)

    disclosure: dict[str, Any] = {
        "stage": stage,
        "commodity": commodity,
        # Single-deal framing — recurring/12-month language is misleading.
        "single_deal": True,
        # Honest BATNA hint — assume multi-origin evaluation by default
        # (users can override on the request inputs if not true).
        "evaluating_origins": True,
        # What we ARE willing to disclose at this stage:
        "volume_disclosure": band,
        "geo_disclosure": region,
        # What we are deliberately holding back at this stage:
        "hold": [],
    }

    # Geo disclosure ladder.
    if stage >= 2 and destination_country:
        disclosure["geo_disclosure"] = destination_country
    if stage >= 4 and destination_port:
        disclosure["geo_disclosure"] = destination_port

    # Volume disclosure ladder. Exact tonnage is only safe at stage >=3
    # where we are anchoring counters to a market reference; even then it
    # appears as "working tonnage", not "12-month rolling".
    if stage >= 3 and isinstance(volume_mt, (int, float)) and volume_mt > 0:
        disclosure["volume_disclosure"] = (
            f"working tonnage {int(volume_mt):,} MT (single deal)"
        )

    # Build the hold list (facts the LLM must NOT leak at this stage).
    hold: list[str] = []
    if stage < 3:
        hold.append("our exact target tonnage")
    if stage < 4 and destination_port:
        hold.append("the destination port")
    if stage < 2 and destination_country:
        hold.append("the destination country (region only)")
    hold.extend(
        [
            "any first-shipment date or specific shipment window",
            "our target price or maximum tolerable price",
            "end-buyer identity",
            "whether the deal is single-shot or recurring",
        ]
    )
    disclosure["hold"] = hold

    return disclosure
