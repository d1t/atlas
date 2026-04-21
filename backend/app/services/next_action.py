"""Next Action Engine.

Rule-based: given the current state of an opportunity (supplier leads, buyer
leads, best match), produce an ordered list of concrete "do this next" actions.

The engine guarantees at least one action at all times, even on an empty
opportunity, so the user never sees "nothing to do".
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.schemas.opportunity import MatchingResult, NextAction, NextActionsOut

_STALE_HOURS = 48
_TARGET_SUPPLIER_COUNT = 3
_TARGET_BUYER_COUNT = 2


def recommend(
    opportunity: Opportunity,
    supplier_leads: list[SupplierLead],
    buyer_leads: list[BuyerLead],
    matches: MatchingResult,
) -> NextActionsOut:
    actions: list[NextAction] = []
    now = datetime.now(UTC)

    active_suppliers = [s for s in supplier_leads if s.status not in ("declined", "lost")]
    active_buyers = [b for b in buyer_leads if b.status not in ("declined", "lost")]

    # 1. Coverage gaps.
    if len(active_suppliers) < _TARGET_SUPPLIER_COUNT:
        shortfall = _TARGET_SUPPLIER_COUNT - len(active_suppliers)
        actions.append(
            NextAction(
                action=f"Source {shortfall} more supplier{'s' if shortfall > 1 else ''}",
                priority="high" if len(active_suppliers) == 0 else "medium",
                reasoning=(
                    f"Only {len(active_suppliers)} active supplier lead(s). "
                    "Aim for at least 3 to keep pricing leverage."
                ),
            )
        )

    if len(active_buyers) < _TARGET_BUYER_COUNT:
        shortfall = _TARGET_BUYER_COUNT - len(active_buyers)
        actions.append(
            NextAction(
                action=f"Engage {shortfall} more buyer{'s' if shortfall > 1 else ''}",
                priority="high" if len(active_buyers) == 0 else "medium",
                reasoning=(
                    f"Only {len(active_buyers)} active buyer lead(s). "
                    "A single buyer leaves you exposed to a walk-away."
                ),
            )
        )

    # 2. Stale supplier follow-ups (contacted but no recent activity).
    for s in active_suppliers:
        if s.last_contacted_at and s.status in ("contacted", "quoted"):
            last = _as_aware(s.last_contacted_at)
            hours = (now - last).total_seconds() / 3600.0
            if hours > _STALE_HOURS and s.price_mt is None:
                actions.append(
                    NextAction(
                        action=f"Follow up {_sname(s)} on quote",
                        priority="high",
                        reasoning=(
                            f"Contacted {hours / 24:.1f} day(s) ago, "
                            "no price received. Re-engage or drop."
                        ),
                    )
                )

    # 3. Buyers with interest but no matching supply.
    for b in active_buyers:
        if b.target_price_mt is not None and b.appetite in ("medium", "high"):
            has_match = any(
                s.price_mt is not None and s.price_mt <= b.target_price_mt
                for s in active_suppliers
            )
            if not has_match and active_suppliers:
                actions.append(
                    NextAction(
                        action=f"Find cheaper supply for {_bname(b)}",
                        priority="high" if b.urgency == "high" else "medium",
                        reasoning=(
                            f"{_bname(b)} targets "
                            f"${b.target_price_mt:,.0f}/MT; "
                            "no current supplier quote is at or below that."
                        ),
                    )
                )

    # 4. Best pair is close to closable — push it.
    best_pair = next((p for p in matches.pairs if p.margin_per_mt > 0), None)
    if best_pair and best_pair.supplier_price_mt and best_pair.buyer_target_price_mt:
        gap_pct = (
            abs(best_pair.margin_per_mt) / best_pair.supplier_price_mt * 100
            if best_pair.supplier_price_mt
            else 0
        )
        if 0 < gap_pct < 2:
            actions.append(
                NextAction(
                    action=(
                        f"Push {best_pair.supplier_name or 'supplier'} / "
                        f"{best_pair.buyer_name or 'buyer'} to close"
                    ),
                    priority="high",
                    reasoning=(
                        "Supplier and buyer are within 2% on price. "
                        "Issue a counter-offer anchored at the midpoint."
                    ),
                )
            )
        elif best_pair.score >= 70:
            actions.append(
                NextAction(
                    action=(
                        f"Promote top match ({best_pair.supplier_name} x "
                        f"{best_pair.buyer_name}) to a Deal"
                    ),
                    priority="medium",
                    reasoning=(
                        f"Score {best_pair.score:.0f}/100, "
                        f"margin ${best_pair.margin_per_mt:,.2f}/MT."
                    ),
                )
            )

    # 5. Suppliers contacted but have not quoted — prompt for quote.
    unquoted = [
        s
        for s in active_suppliers
        if s.status == "contacted" and s.price_mt is None
    ]
    if len(unquoted) >= 2:
        actions.append(
            NextAction(
                action=f"Request quotes from {len(unquoted)} contacted suppliers",
                priority="medium",
                reasoning=(
                    "Multiple suppliers were contacted but haven't given pricing yet."
                ),
            )
        )

    # Fallback — always return at least one action.
    if not actions:
        if opportunity.status == "draft":
            actions.append(
                NextAction(
                    action="Define target price band and destination port",
                    priority="medium",
                    reasoning=(
                        "Opportunity is still in draft. Fill in the target band "
                        "so matches can be scored against it."
                    ),
                )
            )
        else:
            actions.append(
                NextAction(
                    action="Review opportunity status — pipeline appears idle",
                    priority="low",
                    reasoning="No outstanding rule-based action.",
                )
            )

    return NextActionsOut(opportunity_id=opportunity.id, actions=actions)


def _sname(s: SupplierLead) -> str:
    return s.supplier_name or f"Supplier #{s.id}"


def _bname(b: BuyerLead) -> str:
    return b.buyer_name or f"Buyer #{b.id}"


def _as_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
