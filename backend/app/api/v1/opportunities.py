"""V2 opportunity-centric API.

Owns the Opportunity / SupplierLead / BuyerLead entities and exposes the
match + health + next-action engines that operate on them.
"""
import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.data.curated_counterparties import (
    CuratedCounterparty,
    get_curated_counterparties,
)
from app.models.deal import Deal
from app.models.opportunity import (
    BuyerLead,
    Opportunity,
    SupplierLead,
)
from app.models.user import User
from app.schemas.opportunity import (
    BuyerLeadCreate,
    BuyerLeadOut,
    BuyerLeadUpdate,
    CuratedCounterpartyOut,
    CuratedSeedRequest,
    HealthScore,
    MatchingResult,
    NextActionsOut,
    OpportunityCreate,
    OpportunityDashboard,
    OpportunityOut,
    OpportunityUpdate,
    PromoteMatchRequest,
    SupplierLeadCreate,
    SupplierLeadOut,
    SupplierLeadUpdate,
)
from app.services import health as health_service
from app.services import matching as matching_service
from app.services import next_action as next_action_service
from app.services.hunter import HunterClient

logger = logging.getLogger(__name__)

router = APIRouter()


# --- helpers --------------------------------------------------------------------


async def _get_opportunity(db: AsyncSession, opportunity_id: int) -> Opportunity:
    obj = await db.get(Opportunity, opportunity_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return obj


async def _get_supplier_lead(
    db: AsyncSession, opportunity_id: int, lead_id: int
) -> SupplierLead:
    lead = await db.get(SupplierLead, lead_id)
    if lead is None or lead.opportunity_id != opportunity_id:
        raise HTTPException(status_code=404, detail="Supplier lead not found")
    return lead


async def _get_buyer_lead(
    db: AsyncSession, opportunity_id: int, lead_id: int
) -> BuyerLead:
    lead = await db.get(BuyerLead, lead_id)
    if lead is None or lead.opportunity_id != opportunity_id:
        raise HTTPException(status_code=404, detail="Buyer lead not found")
    return lead


async def _load_leads(
    db: AsyncSession, opportunity_id: int
) -> tuple[list[SupplierLead], list[BuyerLead]]:
    sup = (
        await db.execute(
            select(SupplierLead)
            .where(SupplierLead.opportunity_id == opportunity_id)
            .order_by(SupplierLead.created_at.asc())
        )
    ).scalars().all()
    buy = (
        await db.execute(
            select(BuyerLead)
            .where(BuyerLead.opportunity_id == opportunity_id)
            .order_by(BuyerLead.created_at.asc())
        )
    ).scalars().all()
    return list(sup), list(buy)


# --- Opportunity CRUD -----------------------------------------------------------


@router.get("", response_model=list[OpportunityOut])
async def list_opportunities(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[OpportunityOut]:
    rows = (
        await db.execute(select(Opportunity).order_by(Opportunity.created_at.desc()))
    ).scalars().all()
    return [OpportunityOut.model_validate(r) for r in rows]


@router.post("", response_model=OpportunityOut, status_code=201)
async def create_opportunity(
    payload: OpportunityCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OpportunityOut:
    obj = Opportunity(
        **payload.model_dump(exclude_unset=True),
        owner_id=user.id,
        status="draft",
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return OpportunityOut.model_validate(obj)


@router.get("/{opportunity_id}", response_model=OpportunityOut)
async def get_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> OpportunityOut:
    obj = await _get_opportunity(db, opportunity_id)
    return OpportunityOut.model_validate(obj)


@router.patch("/{opportunity_id}", response_model=OpportunityOut)
async def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> OpportunityOut:
    obj = await _get_opportunity(db, opportunity_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return OpportunityOut.model_validate(obj)


@router.delete("/{opportunity_id}", status_code=204)
async def delete_opportunity(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    obj = await _get_opportunity(db, opportunity_id)
    await db.delete(obj)
    await db.commit()


# --- Supplier leads -------------------------------------------------------------


@router.get(
    "/{opportunity_id}/supplier-leads", response_model=list[SupplierLeadOut]
)
async def list_supplier_leads(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SupplierLeadOut]:
    await _get_opportunity(db, opportunity_id)
    rows = (
        await db.execute(
            select(SupplierLead)
            .where(SupplierLead.opportunity_id == opportunity_id)
            .order_by(SupplierLead.created_at.asc())
        )
    ).scalars().all()
    return [SupplierLeadOut.model_validate(r) for r in rows]


@router.post(
    "/{opportunity_id}/supplier-leads",
    response_model=SupplierLeadOut,
    status_code=201,
)
async def create_supplier_lead(
    opportunity_id: int,
    payload: SupplierLeadCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SupplierLeadOut:
    await _get_opportunity(db, opportunity_id)
    lead = SupplierLead(
        opportunity_id=opportunity_id,
        **payload.model_dump(exclude_unset=True),
        status="new",
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return SupplierLeadOut.model_validate(lead)


@router.patch(
    "/{opportunity_id}/supplier-leads/{lead_id}",
    response_model=SupplierLeadOut,
)
async def update_supplier_lead(
    opportunity_id: int,
    lead_id: int,
    payload: SupplierLeadUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SupplierLeadOut:
    lead = await _get_supplier_lead(db, opportunity_id, lead_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(lead, k, v)
    await db.commit()
    await db.refresh(lead)
    return SupplierLeadOut.model_validate(lead)


@router.delete(
    "/{opportunity_id}/supplier-leads/{lead_id}", status_code=204
)
async def delete_supplier_lead(
    opportunity_id: int,
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    lead = await _get_supplier_lead(db, opportunity_id, lead_id)
    await db.delete(lead)
    await db.commit()


# --- Curated counterparties -----------------------------------------------------
#
# For commodity/origin lanes with a small, well-known counterparty universe
# (Brazil raw sugar being the canonical example), a static vetted list beats
# web-search + email enrichment. The registry lives in
# ``app.data.curated_counterparties``; these endpoints expose it on the
# opportunity workspace.


def _already_added_names(leads: list[SupplierLead]) -> set[str]:
    """Names already attached to this opportunity, case-folded for matching."""
    return {(lead.supplier_name or "").strip().casefold() for lead in leads}


def _to_curated_out(
    cp: CuratedCounterparty, already: set[str]
) -> CuratedCounterpartyOut:
    return CuratedCounterpartyOut(
        name=cp.name,
        country=cp.country,
        commodity=cp.commodity,
        website=cp.website,
        type=cp.type,
        description=cp.description,
        already_added=cp.name.strip().casefold() in already,
    )


@router.get(
    "/{opportunity_id}/curated-suppliers",
    response_model=list[CuratedCounterpartyOut],
)
async def list_curated_suppliers(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[CuratedCounterpartyOut]:
    """Preview curated counterparties for this opportunity's commodity.

    Lookup is by commodity (case-insensitive substring match — see
    :func:`app.data.curated_counterparties.get_curated_counterparties`); origin
    is shown to the user per entry. Each entry carries an ``already_added``
    flag so the UI can avoid duplicating existing supplier leads.
    """
    opp = await _get_opportunity(db, opportunity_id)
    curated = get_curated_counterparties(opp.commodity)
    if not curated:
        return []
    sup, _ = await _load_leads(db, opportunity_id)
    already = _already_added_names(sup)
    return [_to_curated_out(cp, already) for cp in curated]


@router.post(
    "/{opportunity_id}/curated-suppliers/seed",
    response_model=list[SupplierLeadOut],
    status_code=201,
)
async def seed_curated_suppliers(
    opportunity_id: int,
    payload: CuratedSeedRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SupplierLeadOut]:
    """Create supplier leads for curated counterparties on this opportunity.

    Idempotent: counterparties already attached (matched by name,
    case-insensitive) are skipped rather than duplicated. Emails are left
    empty — the site-crawler fills them in on first AI Classify pass, which
    avoids shipping guessed role inboxes that may bounce.
    """
    opp = await _get_opportunity(db, opportunity_id)
    curated = get_curated_counterparties(opp.commodity)
    if not curated:
        return []

    requested = {n.strip().casefold() for n in payload.names if n.strip()}
    if requested:
        curated = [c for c in curated if c.name.strip().casefold() in requested]
        if not curated:
            raise HTTPException(
                status_code=404,
                detail="No curated counterparties match the requested names",
            )

    sup, _ = await _load_leads(db, opportunity_id)
    already = _already_added_names(sup)

    created: list[SupplierLead] = []
    for cp in curated:
        if cp.name.strip().casefold() in already:
            continue
        lead = SupplierLead(
            opportunity_id=opportunity_id,
            supplier_name=cp.name,
            country=cp.country,
            email=None,
            notes=f"[Curated] {cp.type} — {cp.description} ({cp.website})",
            status="new",
        )
        db.add(lead)
        created.append(lead)

    if not created:
        # Everything in the curated set was already attached — return empty
        # rather than 409 so the UI can render "already added" without errors.
        return []

    await db.commit()
    for lead in created:
        await db.refresh(lead)

    # Enrich contacts via Hunter.io Domain Search. Best-effort: failures
    # leave the fields as None and the trader can still manually fill them.
    await _enrich_leads_via_hunter(db, created, curated)

    return [SupplierLeadOut.model_validate(lead) for lead in created]


async def _enrich_leads_via_hunter(
    db: AsyncSession,
    leads: list[SupplierLead],
    curated: list[CuratedCounterparty],
) -> None:
    """Best-effort Hunter.io enrichment for freshly-seeded supplier leads."""
    hunter = HunterClient()
    website_by_name = {cp.name.strip().casefold(): cp.website for cp in curated}

    async def _enrich_one(lead: SupplierLead) -> None:
        key = (lead.supplier_name or "").strip().casefold()
        website = website_by_name.get(key)
        if not website:
            return
        contact = await hunter.enrich_domain(website)
        if not contact:
            return
        lead.email = contact.email
        parts = [contact.first_name, contact.last_name]
        lead.contact_name = " ".join(p for p in parts if p) or None
        lead.contact_title = contact.position

    try:
        await asyncio.gather(*[_enrich_one(ld) for ld in leads])
        await db.commit()
        for ld in leads:
            await db.refresh(ld)
    except Exception as exc:
        logger.warning("Hunter.io enrichment failed: %s", exc)


# --- Buyer leads ----------------------------------------------------------------


@router.get("/{opportunity_id}/buyer-leads", response_model=list[BuyerLeadOut])
async def list_buyer_leads(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[BuyerLeadOut]:
    await _get_opportunity(db, opportunity_id)
    rows = (
        await db.execute(
            select(BuyerLead)
            .where(BuyerLead.opportunity_id == opportunity_id)
            .order_by(BuyerLead.created_at.asc())
        )
    ).scalars().all()
    return [BuyerLeadOut.model_validate(r) for r in rows]


@router.post(
    "/{opportunity_id}/buyer-leads",
    response_model=BuyerLeadOut,
    status_code=201,
)
async def create_buyer_lead(
    opportunity_id: int,
    payload: BuyerLeadCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BuyerLeadOut:
    await _get_opportunity(db, opportunity_id)
    lead = BuyerLead(
        opportunity_id=opportunity_id,
        **payload.model_dump(exclude_unset=True),
        status="new",
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return BuyerLeadOut.model_validate(lead)


@router.patch(
    "/{opportunity_id}/buyer-leads/{lead_id}", response_model=BuyerLeadOut
)
async def update_buyer_lead(
    opportunity_id: int,
    lead_id: int,
    payload: BuyerLeadUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> BuyerLeadOut:
    lead = await _get_buyer_lead(db, opportunity_id, lead_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(lead, k, v)
    await db.commit()
    await db.refresh(lead)
    return BuyerLeadOut.model_validate(lead)


@router.delete(
    "/{opportunity_id}/buyer-leads/{lead_id}", status_code=204
)
async def delete_buyer_lead(
    opportunity_id: int,
    lead_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    lead = await _get_buyer_lead(db, opportunity_id, lead_id)
    await db.delete(lead)
    await db.commit()


# --- Derived views --------------------------------------------------------------


@router.get("/{opportunity_id}/matches", response_model=MatchingResult)
async def get_matches(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> MatchingResult:
    opp = await _get_opportunity(db, opportunity_id)
    sup, buy = await _load_leads(db, opportunity_id)
    return matching_service.rank_pairs(opp, sup, buy)


@router.get("/{opportunity_id}/health", response_model=HealthScore)
async def get_health(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> HealthScore:
    opp = await _get_opportunity(db, opportunity_id)
    sup, buy = await _load_leads(db, opportunity_id)
    matches = matching_service.rank_pairs(opp, sup, buy)
    return health_service.score(opp, sup, buy, matches)


@router.get("/{opportunity_id}/next-actions", response_model=NextActionsOut)
async def get_next_actions(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> NextActionsOut:
    opp = await _get_opportunity(db, opportunity_id)
    sup, buy = await _load_leads(db, opportunity_id)
    matches = matching_service.rank_pairs(opp, sup, buy)
    return next_action_service.recommend(opp, sup, buy, matches)


@router.get(
    "/{opportunity_id}/dashboard", response_model=OpportunityDashboard
)
async def get_dashboard(
    opportunity_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> OpportunityDashboard:
    opp = await _get_opportunity(db, opportunity_id)
    sup, buy = await _load_leads(db, opportunity_id)
    matches = matching_service.rank_pairs(opp, sup, buy)
    health = health_service.score(opp, sup, buy, matches)
    actions = next_action_service.recommend(opp, sup, buy, matches)
    return OpportunityDashboard(
        opportunity=OpportunityOut.model_validate(opp),
        supplier_leads=[SupplierLeadOut.model_validate(s) for s in sup],
        buyer_leads=[BuyerLeadOut.model_validate(b) for b in buy],
        matches=matches,
        health=health,
        next_actions=actions,
    )


# --- Promote a match into a deal ------------------------------------------------


@router.post("/{opportunity_id}/deals", response_model=dict, status_code=201)
async def promote_match_to_deal(
    opportunity_id: int,
    payload: PromoteMatchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Create a Deal row from a chosen supplier-lead x buyer-lead pair.

    The Deal is the 'execution record' for a committed match — everything downstream
    (SPA, LC, shipment) continues to live on the existing Deal entity, but it's now
    linked back to the originating opportunity + lead rows.
    """
    opp = await _get_opportunity(db, opportunity_id)
    sup = await _get_supplier_lead(db, opportunity_id, payload.supplier_lead_id)
    buy = await _get_buyer_lead(db, opportunity_id, payload.buyer_lead_id)

    buy_price = sup.price_mt or 0.0
    sell_price = buy.target_price_mt or buy_price
    volume = min(
        v
        for v in (opp.volume_mt, sup.min_order_mt, buy.volume_mt)
        if v
    ) if any((opp.volume_mt, sup.min_order_mt, buy.volume_mt)) else opp.volume_mt or 0.0
    margin = max(0.0, sell_price - buy_price)

    title = (
        payload.title
        or f"{opp.commodity.title()} — {sup.supplier_name or 'supplier'} "
        f"to {buy.buyer_name or 'buyer'}"
    )

    deal = Deal(
        title=title,
        commodity=opp.commodity,
        volume_mt=volume,
        buy_price=buy_price,
        sell_price=sell_price,
        incoterms=sup.quoted_incoterms or opp.incoterms,
        currency=opp.currency,
        stage="pricing",
        supplier_id=sup.supplier_id,
        buyer_id=buy.buyer_id,
        owner_id=user.id,
        opportunity_id=opportunity_id,
        supplier_lead_id=sup.id,
        buyer_lead_id=buy.id,
        margin_per_mt=margin,
        total_value=sell_price * volume,
        total_margin=margin * volume,
    )
    db.add(deal)

    # Mark the opportunity and the chosen leads as matched.
    opp.status = "matched"
    sup.status = "shortlisted"
    buy.status = "committed"

    # Timestamp the contact on both leads (you've just decided to execute with them).
    now = datetime.now(UTC)
    sup.last_contacted_at = sup.last_contacted_at or now
    buy.last_contacted_at = buy.last_contacted_at or now

    await db.commit()
    await db.refresh(deal)

    return {
        "deal_id": deal.id,
        "opportunity_id": opportunity_id,
        "supplier_lead_id": sup.id,
        "buyer_lead_id": buy.id,
        "title": deal.title,
        "buy_price": deal.buy_price,
        "sell_price": deal.sell_price,
        "volume_mt": deal.volume_mt,
        "margin_per_mt": deal.margin_per_mt,
        "total_margin": deal.total_margin,
    }
