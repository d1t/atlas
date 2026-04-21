"""Supplier x Buyer matching engine.

For a given opportunity, enumerate all (supplier_lead, buyer_lead) pairs and
rank them by a composite score combining:
  - margin per MT (must be positive to be 'viable')
  - supplier credibility
  - supplier responsiveness
  - buyer appetite + urgency
  - price alignment vs the opportunity's target range (if set)

Rule-based; no LLM involvement. Deliberately simple and explainable.
"""
from __future__ import annotations

from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.schemas.opportunity import MatchingResult, MatchPair

_APPETITE_WEIGHT = {"low": 0.4, "medium": 0.7, "high": 1.0}
_URGENCY_WEIGHT = {"low": 0.4, "medium": 0.7, "high": 1.0}


def rank_pairs(
    opportunity: Opportunity,
    supplier_leads: list[SupplierLead],
    buyer_leads: list[BuyerLead],
) -> MatchingResult:
    pairs: list[MatchPair] = []
    viable = 0

    # Opportunity volume is the reference for "total margin"; falls back to the
    # lesser of supplier MOQ and buyer volume when set at pair level.
    opp_volume = opportunity.volume_mt or 0.0

    for s in supplier_leads:
        for b in buyer_leads:
            s_price = s.price_mt
            b_price = b.target_price_mt
            reasoning: list[str] = []

            if s_price is None or b_price is None:
                # Can't compute a margin yet — include the pair with neutral score
                # so the UI can still surface it as "needs quote".
                pair = MatchPair(
                    supplier_lead_id=s.id,
                    supplier_name=_supplier_label(s),
                    supplier_price_mt=s_price,
                    buyer_lead_id=b.id,
                    buyer_name=_buyer_label(b),
                    buyer_target_price_mt=b_price,
                    margin_per_mt=0.0,
                    total_margin=None,
                    score=0.0,
                    reasoning=[
                        "Waiting on "
                        + (
                            "supplier quote"
                            if s_price is None
                            else "buyer target price"
                        )
                    ],
                )
                pairs.append(pair)
                continue

            margin_per_mt = b_price - s_price
            pair_volume = _pair_volume(opp_volume, s, b)
            total_margin = margin_per_mt * pair_volume if pair_volume else None

            # --- score components (each 0..1) ---

            # Margin component: 0 at break-even, 1 at >=10% of the supplier price.
            if s_price > 0:
                margin_ratio = max(0.0, margin_per_mt / s_price)
                margin_component = min(margin_ratio / 0.10, 1.0)
            else:
                margin_component = 1.0 if margin_per_mt > 0 else 0.0
            reasoning.append(
                f"margin: ${margin_per_mt:,.2f}/MT "
                f"({margin_ratio * 100:,.1f}% over cost)"
                if s_price > 0
                else f"margin: ${margin_per_mt:,.2f}/MT"
            )

            # Credibility + responsiveness (each 0..100 -> 0..1).
            credibility_component = _clamp01((s.credibility_score or 50) / 100.0)
            responsiveness_component = _clamp01((s.responsiveness_score or 50) / 100.0)

            # Buyer signal: appetite * urgency.
            buyer_component = (
                _APPETITE_WEIGHT.get(b.appetite, 0.6)
                * _URGENCY_WEIGHT.get(b.urgency, 0.6)
            )

            # Price-vs-target-range alignment (opportunity's band).
            alignment_component = _alignment(opportunity, s_price, b_price)
            if opportunity.target_price_max and opportunity.target_price_min:
                reasoning.append(
                    f"target band: ${opportunity.target_price_min:,.0f}-"
                    f"${opportunity.target_price_max:,.0f}/MT"
                )

            # Weighted composite (weights sum to 1.0).
            score01 = (
                0.45 * margin_component
                + 0.15 * credibility_component
                + 0.10 * responsiveness_component
                + 0.15 * buyer_component
                + 0.15 * alignment_component
            )
            score = round(score01 * 100.0, 1)

            if margin_per_mt > 0:
                viable += 1

            if credibility_component < 0.4:
                reasoning.append("low supplier credibility — verify before committing")
            if responsiveness_component < 0.4:
                reasoning.append("slow supplier responses — expect drag")
            if b.appetite == "high":
                reasoning.append("buyer appetite HIGH")
            if b.urgency == "high":
                reasoning.append("buyer urgency HIGH")

            pairs.append(
                MatchPair(
                    supplier_lead_id=s.id,
                    supplier_name=_supplier_label(s),
                    supplier_price_mt=s_price,
                    buyer_lead_id=b.id,
                    buyer_name=_buyer_label(b),
                    buyer_target_price_mt=b_price,
                    margin_per_mt=round(margin_per_mt, 2),
                    total_margin=(
                        round(total_margin, 2) if total_margin is not None else None
                    ),
                    score=score,
                    reasoning=reasoning,
                )
            )

    pairs.sort(key=lambda p: p.score, reverse=True)
    return MatchingResult(
        opportunity_id=opportunity.id,
        total_pairs=len(pairs),
        viable_pairs=viable,
        pairs=pairs,
    )


def _pair_volume(opp_volume: float, s: SupplierLead, b: BuyerLead) -> float:
    """Effective volume usable by this pair."""
    candidates = [v for v in (opp_volume, s.min_order_mt, b.volume_mt) if v]
    return min(candidates) if candidates else 0.0


def _alignment(opp: Opportunity, supplier_price: float, buyer_price: float) -> float:
    lo = opp.target_price_min
    hi = opp.target_price_max
    if lo is None or hi is None or hi <= lo:
        return 0.6  # neutral when the user hasn't set a band
    midpoint = (lo + hi) / 2.0
    band_half = (hi - lo) / 2.0
    mid_pair = (supplier_price + buyer_price) / 2.0
    dev = abs(mid_pair - midpoint) / band_half if band_half else 1.0
    return _clamp01(1.0 - dev)


def _clamp01(x: float) -> float:
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return x


def _supplier_label(s: SupplierLead) -> str | None:
    return s.supplier_name or (f"Supplier #{s.id}" if s.id else None)


def _buyer_label(b: BuyerLead) -> str | None:
    return b.buyer_name or (f"Buyer #{b.id}" if b.id else None)
