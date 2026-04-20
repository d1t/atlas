"""Module 3: Deal Structuring Engine.

Pure, deterministic pricing + scenario simulation. No LLM in the hot path —
traders must trust the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PricingInputs:
    buy_price: float
    sell_price: float
    freight_estimate: float
    volume_mt: float
    incoterms: str | None = None


@dataclass
class PricingOutputs:
    margin_per_mt: float
    total_value: float
    total_margin: float
    recommended_structure: str
    rationale: str
    scenarios: list[dict]


def compute_pricing(inp: PricingInputs) -> PricingOutputs:
    volume = max(0.0, inp.volume_mt)
    margin_per_mt = inp.sell_price - inp.buy_price - inp.freight_estimate
    total_value = inp.sell_price * volume
    total_margin = margin_per_mt * volume

    structure, rationale = _recommend_structure(margin_per_mt, total_margin, volume)

    scenarios = [
        _scenario("base", inp, price_delta_pct=0.0, freight_delta_pct=0.0),
        _scenario("bull", inp, price_delta_pct=+5.0, freight_delta_pct=-10.0),
        _scenario("bear", inp, price_delta_pct=-5.0, freight_delta_pct=+10.0),
        _scenario("stressed", inp, price_delta_pct=-10.0, freight_delta_pct=+20.0),
    ]

    return PricingOutputs(
        margin_per_mt=round(margin_per_mt, 4),
        total_value=round(total_value, 2),
        total_margin=round(total_margin, 2),
        recommended_structure=structure,
        rationale=rationale,
        scenarios=scenarios,
    )


def _recommend_structure(
    margin_per_mt: float, total_margin: float, volume: float
) -> tuple[str, str]:
    if margin_per_mt <= 0:
        return (
            "brokerage",
            "Margin is non-positive on a principal basis; pivot to brokerage "
            "(per-MT commission) to avoid balance-sheet risk.",
        )
    if volume >= 5000 and margin_per_mt < 10:
        return (
            "back_to_back_lc",
            "Large volume with thin per-MT margin. Back-to-back LC minimises working "
            "capital lock-up and credit exposure.",
        )
    if total_margin >= 25_000 and margin_per_mt >= 15:
        return (
            "principal",
            "Strong absolute and unit margin — principal trade captures full upside.",
        )
    return (
        "back_to_back_lc",
        "Moderate margin — back-to-back LC balances upside with minimal capital exposure.",
    )


def _scenario(name: str, inp: PricingInputs, price_delta_pct: float, freight_delta_pct: float) -> dict:
    sell = inp.sell_price * (1 + price_delta_pct / 100)
    freight = inp.freight_estimate * (1 + freight_delta_pct / 100)
    margin = sell - inp.buy_price - freight
    return {
        "name": name,
        "sell_price": round(sell, 4),
        "freight": round(freight, 4),
        "margin_per_mt": round(margin, 4),
        "total_margin": round(margin * inp.volume_mt, 2),
    }
