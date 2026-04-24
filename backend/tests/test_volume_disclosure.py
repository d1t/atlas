"""Unit tests for the stage-gated opportunity disclosure helper.

The helper is the single source of truth for what we tell suppliers vs what
we hold back at each negotiation stage. These tests assert the leverage-
preserving guarantees: stage-1 emails never see the raw tonnage or port,
stage-3 sees the exact tonnage as a working figure, stage-4 sees the port.
"""
from __future__ import annotations

from app.ai.volume_disclosure import (
    build_opportunity_disclosure,
    derive_volume_band,
    region_for,
)


def test_derive_volume_band_falls_back_when_missing():
    assert derive_volume_band(None) is None
    assert derive_volume_band(0) is None
    assert derive_volume_band(-100) is None


def test_derive_volume_band_anchors_low():
    """The band must START low so the supplier anchors against the floor."""
    assert "5,000-15,000" in (derive_volume_band(10_000) or "")
    assert "15,000-40,000" in (derive_volume_band(30_000) or "")
    assert "25,000-55,000" in (derive_volume_band(50_000) or "")
    assert "50,000-80,000" in (derive_volume_band(120_000) or "")


def test_region_for_known_country_uses_region_label():
    assert region_for("Nigeria") == "West Africa"
    assert region_for("nigeria") == "West Africa"
    assert region_for("Brazil") == "South America"
    assert region_for("Indonesia") == "Southeast Asia"


def test_region_for_unknown_country_falls_back_safely():
    assert region_for(None) == "the destination region"
    assert region_for("") == "the destination region"
    assert region_for("Atlantis") == "the destination region"


def test_stage1_holds_back_volume_and_port():
    d = build_opportunity_disclosure(
        stage=1,
        volume_mt=50_000,
        destination_country="Nigeria",
        destination_port="Lagos",
        commodity="sugar",
    )

    # We disclose the BAND, not the exact tonnage.
    assert "50,000 MT" not in d["volume_disclosure"]
    assert "exploratory" in d["volume_disclosure"]

    # We disclose the REGION, not the country or port.
    assert d["geo_disclosure"] == "West Africa"

    assert d["single_deal"] is True
    assert d["evaluating_origins"] is True

    holds = " | ".join(d["hold"])
    assert "exact target tonnage" in holds
    assert "destination port" in holds


def test_stage2_reveals_country_but_still_holds_back_port_and_tonnage():
    d = build_opportunity_disclosure(
        stage=2,
        volume_mt=50_000,
        destination_country="Nigeria",
        destination_port="Lagos",
        commodity="sugar",
    )
    assert d["geo_disclosure"] == "Nigeria"
    assert "50,000 MT" not in d["volume_disclosure"]
    holds = " | ".join(d["hold"])
    assert "exact target tonnage" in holds
    assert "destination port" in holds


def test_stage3_reveals_working_tonnage_but_not_port():
    d = build_opportunity_disclosure(
        stage=3,
        volume_mt=50_000,
        destination_country="Nigeria",
        destination_port="Lagos",
        commodity="sugar",
    )
    # At stage 3 the supplier has quoted, intel exists, and the working
    # tonnage is now safe to anchor a counter against.
    assert "50,000" in d["volume_disclosure"]
    assert "single deal" in d["volume_disclosure"]
    # Country yes, port no.
    assert d["geo_disclosure"] == "Nigeria"
    assert "destination port" in " | ".join(d["hold"])


def test_stage4_reveals_port():
    d = build_opportunity_disclosure(
        stage=4,
        volume_mt=50_000,
        destination_country="Nigeria",
        destination_port="Lagos",
        commodity="sugar",
    )
    assert d["geo_disclosure"] == "Lagos"
    assert "destination port" not in " | ".join(d["hold"])
