"""Deal Health Score for an opportunity.

Produces an explainable 0-100 viability score from five weighted factors:
  - supplier coverage
  - buyer coverage
  - price alignment (best pair's margin relative to opportunity volume)
  - supplier responsiveness (mean across leads)
  - freshness (time since last contact)

Kept deliberately simple and rule-based. No LLM.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.schemas.opportunity import HealthFactor, HealthScore, MatchingResult

_TARGET_SUPPLIER_COUNT = 3
_TARGET_BUYER_COUNT = 2
_FRESH_DAYS = 7  # anything older than this starts losing points


def score(
    opportunity: Opportunity,
    supplier_leads: list[SupplierLead],
    buyer_leads: list[BuyerLead],
    matches: MatchingResult,
) -> HealthScore:
    factors: list[HealthFactor] = []

    # 1. Supplier coverage (need at least 3 active candidates to have leverage).
    active_suppliers = [s for s in supplier_leads if s.status not in ("declined", "lost")]
    sup_ratio = min(len(active_suppliers) / _TARGET_SUPPLIER_COUNT, 1.0)
    factors.append(
        HealthFactor(
            name="Supplier coverage",
            weight=0.25,
            value=round(sup_ratio, 2),
            contribution=round(0.25 * sup_ratio * 100, 1),
            detail=(
                f"{len(active_suppliers)} active supplier lead(s); "
                f"target {_TARGET_SUPPLIER_COUNT}"
            ),
        )
    )

    # 2. Buyer coverage.
    active_buyers = [b for b in buyer_leads if b.status not in ("declined", "lost")]
    buy_ratio = min(len(active_buyers) / _TARGET_BUYER_COUNT, 1.0)
    factors.append(
        HealthFactor(
            name="Buyer coverage",
            weight=0.20,
            value=round(buy_ratio, 2),
            contribution=round(0.20 * buy_ratio * 100, 1),
            detail=(
                f"{len(active_buyers)} active buyer lead(s); "
                f"target {_TARGET_BUYER_COUNT}"
            ),
        )
    )

    # 3. Best match strength — use the top-ranked viable pair's normalized score.
    best_pair = next((p for p in matches.pairs if p.margin_per_mt > 0), None)
    if best_pair is None:
        best_value = 0.0
        best_detail = "no viable supplier-buyer pair yet"
    else:
        best_value = round(best_pair.score / 100.0, 2)
        best_detail = (
            f"top pair score {best_pair.score:.0f}/100, "
            f"margin ${best_pair.margin_per_mt:,.2f}/MT"
        )
    factors.append(
        HealthFactor(
            name="Price alignment",
            weight=0.30,
            value=best_value,
            contribution=round(0.30 * best_value * 100, 1),
            detail=best_detail,
        )
    )

    # 4. Mean supplier responsiveness.
    if active_suppliers:
        mean_resp = sum(s.responsiveness_score for s in active_suppliers) / len(
            active_suppliers
        )
    else:
        mean_resp = 0
    resp_value = round(mean_resp / 100.0, 2)
    factors.append(
        HealthFactor(
            name="Supplier responsiveness",
            weight=0.15,
            value=resp_value,
            contribution=round(0.15 * resp_value * 100, 1),
            detail=f"mean responsiveness {mean_resp:.0f}/100",
        )
    )

    # 5. Freshness — penalise if suppliers + buyers haven't been contacted recently.
    now = datetime.now(UTC)
    all_leads: list[SupplierLead | BuyerLead] = [*active_suppliers, *active_buyers]
    contacted = [
        lead.last_contacted_at for lead in all_leads if lead.last_contacted_at
    ]
    if contacted:
        latest = max(contacted)
        # make the comparison timezone-aware (SQLite may return naive datetimes)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        days_since = (now - latest).days
        fresh_value = max(0.0, 1.0 - days_since / _FRESH_DAYS)
        fresh_detail = f"{days_since} day(s) since last contact"
    else:
        fresh_value = 0.0
        fresh_detail = "no recorded contact yet"
    factors.append(
        HealthFactor(
            name="Engagement freshness",
            weight=0.10,
            value=round(fresh_value, 2),
            contribution=round(0.10 * fresh_value * 100, 1),
            detail=fresh_detail,
        )
    )

    total = int(round(sum(f.contribution for f in factors)))
    total = max(0, min(100, total))

    if total >= 70:
        status = "Viable"
        rec = "Push to close. Issue LOI / NCNDA and advance to SPA stage."
    elif total >= 45:
        status = "Moderately viable"
        rec = _moderate_recommendation(factors, matches)
    else:
        status = "Weak"
        rec = _weak_recommendation(factors)

    return HealthScore(
        opportunity_id=opportunity.id,
        score=total,
        status=status,
        factors=factors,
        recommendation=rec,
    )


def _moderate_recommendation(
    factors: list[HealthFactor], matches: MatchingResult
) -> str:
    # Find the weakest factor and recommend the counter-move.
    weakest = min(factors, key=lambda f: f.value)
    if weakest.name == "Supplier coverage":
        return "Source 2-3 more supplier quotes to strengthen negotiating position."
    if weakest.name == "Buyer coverage":
        return "Engage additional buyers — single buyer = single point of failure."
    if weakest.name == "Price alignment":
        return "Push supplier on price or renegotiate buyer target; current margin is thin."
    if weakest.name == "Supplier responsiveness":
        return "Chase slow suppliers or drop them from the shortlist."
    if weakest.name == "Engagement freshness":
        return "Follow up on stale conversations — you are losing ground to competing buyers."
    return "Keep working the weakest factor above."


def _weak_recommendation(factors: list[HealthFactor]) -> str:
    missing = [f.name for f in factors if f.value < 0.3]
    if missing:
        return "Critical gap(s): " + ", ".join(missing) + ". Resolve before further outreach."
    return "Insufficient data — add supplier and buyer leads to progress."
