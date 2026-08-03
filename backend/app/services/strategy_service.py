"""Strategy engine — turns a north-star goal into pillar objectives and a
week-by-week / day-to-day cadence.

Two layers:

* **Planning** (``draft_pillars``): asks the LLM to break the north star into
  objectives across the four value-chain pillars, with a deterministic fallback
  so it works with the mock provider / offline.
* **Cadence** (``generate_plan`` + ``build_board``): reads the live state of the
  user's opportunities (reusing the matching + next-action engines) and emits
  concrete ``StrategyTask`` rows for the week, then rolls everything up into a
  pillar-health board.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import get_llm
from app.core.config import get_settings
from app.models.email import EmailMessage
from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.models.strategy import (
    PILLAR_LABELS,
    PILLARS,
    Strategy,
    StrategyTask,
)
from app.models.supplier import Supplier
from app.schemas.strategy import PillarProgress
from app.services import email_service
from app.services import matching as matching_service
from app.services import next_action as next_action_service
from app.services.counterparty import CounterpartyService
from app.services.supplier_discovery import SupplierCandidate, SupplierDiscoveryService

logger = logging.getLogger("atlas.strategy")

# Default pillar coverage targets when the user hasn't set their own.
_DEFAULT_TARGETS = {
    "origination": 3,   # active opportunities in flight
    "demand": 3,        # active buyer leads across opportunities
    "supply": 5,        # active supplier leads across opportunities
    "execution": 1,     # matches promoted toward a deal
}

_DEAD_STATUSES = ("declined", "lost")

# A lead that was contacted but has gone quiet for this many days earns a
# follow-up task in the weekly cadence.
_FOLLOWUP_STALE_DAYS = 3

# The 1-5 bargaining stages from :mod:`app.ai.negotiation_strategy`, surfaced in
# close-task detail so the trader knows exactly where each counterparty sits.
_NEGOTIATION_STAGE_LABELS = {
    1: "Cold outreach",
    2: "First response / SCO",
    3: "Counter-offer",
    4: "Terms negotiation",
    5: "Close / SPA",
}


# --- Planning -------------------------------------------------------------


def _fallback_pillars(strategy: Strategy) -> dict:
    commodity = strategy.commodity or "the commodity"
    origin = strategy.origin_region or "target origin"
    dest = strategy.destination_region or "target market"
    vol = strategy.target_volume_mt
    margin = strategy.target_margin_per_mt
    vol_txt = f"{vol:,.0f} MT" if vol else "target volume"
    margin_txt = f"${margin:,.0f}/MT" if margin else "target margin"
    return {
        "origination": {
            "objective": (
                f"Build a repeatable pipeline of {commodity} trade ideas linking "
                f"{origin} supply to {dest} demand."
            ),
            "kpi": "Active opportunities",
            "target": _DEFAULT_TARGETS["origination"],
        },
        "demand": {
            "objective": (
                f"Secure committed buyers in {dest} to underwrite {vol_txt} of "
                f"off-take."
            ),
            "kpi": "Active buyer leads",
            "target": _DEFAULT_TARGETS["demand"],
        },
        "supply": {
            "objective": (
                f"Qualify credible {commodity} suppliers in {origin} with FOB/CFR "
                f"pricing and NCNDA readiness."
            ),
            "kpi": "Active supplier leads",
            "target": _DEFAULT_TARGETS["supply"],
        },
        "execution": {
            "objective": (
                f"Negotiate and close matched deals at {margin_txt} margin, driving "
                f"each to SPA + LC."
            ),
            "kpi": "Deals in execution",
            "target": _DEFAULT_TARGETS["execution"],
        },
    }


async def draft_pillars(strategy: Strategy) -> dict:
    """Ask the LLM to draft four-pillar objectives; fall back deterministically."""
    fallback = _fallback_pillars(strategy)
    llm = get_llm()
    system = (
        "You are the head of trading strategy for a commodity trade house. Break a "
        "north-star goal into concrete objectives across FOUR pillars: origination, "
        "demand (buy-side/off-take), supply (sell-side origin), and execution "
        "(negotiate + close). Reply with ONLY a JSON object keyed by pillar; each "
        "value is an object with keys 'objective' (one sentence), 'kpi' (a countable "
        "metric name), and 'target' (an integer). No prose, no code fences."
    )
    user = json.dumps(
        {
            "title": strategy.title,
            "north_star": strategy.north_star,
            "commodity": strategy.commodity,
            "origin_region": strategy.origin_region,
            "destination_region": strategy.destination_region,
            "horizon": strategy.horizon,
            "target_volume_mt": strategy.target_volume_mt,
            "target_margin_per_mt": strategy.target_margin_per_mt,
        },
        default=str,
    )
    try:
        raw = await llm.complete(system, user, max_tokens=600)
        parsed = json.loads(_strip_fences(raw))
        result: dict = {}
        for pillar in PILLARS:
            block = parsed.get(pillar) if isinstance(parsed, dict) else None
            if isinstance(block, dict) and block.get("objective"):
                result[pillar] = {
                    "objective": str(block.get("objective")),
                    "kpi": str(block.get("kpi") or fallback[pillar]["kpi"]),
                    "target": _coerce_int(
                        block.get("target"), fallback[pillar]["target"]
                    ),
                }
            else:
                result[pillar] = fallback[pillar]
        return result
    except Exception:
        logger.info("Strategy pillar draft fell back to deterministic template")
        return fallback


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# --- Cadence --------------------------------------------------------------


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def _relevant_opportunities(
    db: AsyncSession, strategy: Strategy
) -> list[Opportunity]:
    stmt = select(Opportunity).where(Opportunity.status.notin_(("closed", "lost")))
    if strategy.commodity:
        stmt = stmt.where(Opportunity.commodity.ilike(strategy.commodity))
    rows = (await db.execute(stmt.order_by(Opportunity.created_at.desc()))).scalars().all()
    return list(rows)


async def _load_leads(
    db: AsyncSession, opportunity_id: int
) -> tuple[list[SupplierLead], list[BuyerLead]]:
    sup = (
        await db.execute(
            select(SupplierLead).where(SupplierLead.opportunity_id == opportunity_id)
        )
    ).scalars().all()
    buy = (
        await db.execute(
            select(BuyerLead).where(BuyerLead.opportunity_id == opportunity_id)
        )
    ).scalars().all()
    return list(sup), list(buy)


def _classify_pillar(action_text: str) -> str:
    t = action_text.lower()
    if any(k in t for k in ("buyer", "engage", "demand", "off-take", "offtake")):
        return "demand"
    if any(k in t for k in ("source", "supplier", "supply", "quote")):
        return "supply"
    if any(k in t for k in ("promote", "close", "push", "deal", "spa", "counter")):
        return "execution"
    return "origination"


async def generate_plan(
    db: AsyncSession, strategy: Strategy, week_start: date | None = None
) -> list[StrategyTask]:
    """Regenerate the auto cadence tasks for a given week.

    Existing *auto* tasks for the week are cleared and rebuilt; *manual* tasks
    are preserved. Tasks are derived from each relevant opportunity's
    next-actions plus standing per-pillar coverage checks.
    """
    week = _monday(week_start or datetime.now(UTC).date())

    # Clear prior auto tasks for this week (idempotent regeneration).
    existing = (
        await db.execute(
            select(StrategyTask).where(
                StrategyTask.strategy_id == strategy.id,
                StrategyTask.week_start == week,
                StrategyTask.source == "auto",
            )
        )
    ).scalars().all()
    for t in existing:
        await db.delete(t)

    opportunities = await _relevant_opportunities(db, strategy)
    pillars = strategy.pillars or _fallback_pillars(strategy)
    now = datetime.now(UTC)

    tasks: list[StrategyTask] = []
    seen: set[tuple[str, str, int | None]] = set()

    def add(
        pillar: str,
        title: str,
        *,
        detail: str | None = None,
        priority: str = "medium",
        cadence: str = "weekly",
        opportunity_id: int | None = None,
        supplier_lead_id: int | None = None,
        buyer_lead_id: int | None = None,
        due_offset: int = 4,
    ) -> None:
        key = (pillar, title, opportunity_id)
        if key in seen:
            return
        seen.add(key)
        due = datetime.combine(
            week + timedelta(days=min(4, due_offset)), time(17, 0), tzinfo=UTC
        )
        tasks.append(
            StrategyTask(
                strategy_id=strategy.id,
                pillar=pillar,
                title=title,
                detail=detail,
                cadence=cadence,
                priority=priority,
                status="todo",
                week_start=week,
                due_at=due,
                opportunity_id=opportunity_id,
                supplier_lead_id=supplier_lead_id,
                buyer_lead_id=buyer_lead_id,
                source="auto",
            )
        )

    # 1. Standing origination check: enough opportunities in flight?
    origination_target = _coerce_int(
        (pillars.get("origination") or {}).get("target"),
        _DEFAULT_TARGETS["origination"],
    )
    if len(opportunities) < origination_target:
        shortfall = origination_target - len(opportunities)
        add(
            "origination",
            f"Originate {shortfall} new {strategy.commodity or 'trade'} "
            f"opportunit{'ies' if shortfall > 1 else 'y'}",
            detail=(
                f"Only {len(opportunities)} active opportunit"
                f"{'ies' if len(opportunities) != 1 else 'y'} vs target "
                f"{origination_target}. Frame new trade ideas linking origin supply "
                f"to destination demand."
            ),
            priority="high",
            due_offset=1,
        )

    def _is_stale(dt: datetime | None) -> bool:
        if dt is None:
            return True
        aware = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return (now - aware).days >= _FOLLOWUP_STALE_DAYS

    # 2. Per-opportunity cadence — generic next-actions plus dedicated
    #    buy-side outreach and deal/close tasks tied to the pipeline stage.
    for opp in opportunities:
        sup, buy = await _load_leads(db, opp.id)
        matches = matching_service.rank_pairs(opp, sup, buy)
        recs = next_action_service.recommend(opp, sup, buy, matches)
        for rec in recs.actions[:4]:
            pillar = _classify_pillar(rec.action)
            add(
                pillar,
                f"{rec.action} — {opp.title}",
                detail=rec.reasoning,
                priority=rec.priority,
                opportunity_id=opp.id,
                due_offset=1 if rec.priority == "high" else 3,
            )

        # 2a. Buy-side outreach cadence — one demand task per live buyer lead,
        #     stepped to where the buyer sits in the funnel.
        for b in buy:
            if b.status in _DEAD_STATUSES or b.status == "committed":
                continue
            name = b.buyer_name or f"Buyer #{b.id}"
            if b.status == "new":
                add(
                    "demand",
                    f"Open buy-side outreach to {name} — {opp.title}",
                    detail=(
                        "New buyer lead — send an intro, gauge appetite and confirm "
                        "target price + volume to underwrite off-take."
                    ),
                    priority="high",
                    opportunity_id=opp.id,
                    buyer_lead_id=b.id,
                    due_offset=1,
                )
            elif b.status == "contacted" and _is_stale(b.last_contacted_at):
                add(
                    "demand",
                    f"Follow up {name} on interest — {opp.title}",
                    detail=(
                        "Contacted but not yet engaged — chase for a firm bid and "
                        "their target price band."
                    ),
                    priority="medium",
                    opportunity_id=opp.id,
                    buyer_lead_id=b.id,
                    due_offset=2,
                )
            elif b.status == "engaged":
                add(
                    "demand",
                    f"Convert {name} to a committed off-take — {opp.title}",
                    detail=(
                        "Buyer is engaged — push for a firm bid / LOI to lock the "
                        "demand side before committing supply."
                    ),
                    priority="high",
                    opportunity_id=opp.id,
                    buyer_lead_id=b.id,
                    due_offset=2,
                )

        # 2b. Deal / close cadence — execution tasks driven by each supplier
        #     lead's negotiation stage and the opportunity's pipeline status.
        for s in sup:
            if s.status in _DEAD_STATUSES:
                continue
            stage = s.negotiation_stage or 1
            name = s.supplier_name or f"Supplier #{s.id}"
            label = _NEGOTIATION_STAGE_LABELS.get(stage, f"stage {stage}")
            if stage >= 4:
                add(
                    "execution",
                    f"Drive {name} to SPA/LC close — {opp.title}",
                    detail=(
                        f"Supplier at '{label}'. Issue the next commitment: circulate "
                        "the draft SPA, agree LC terms and lock the delivery window."
                    ),
                    priority="high",
                    opportunity_id=opp.id,
                    supplier_lead_id=s.id,
                    due_offset=2,
                )
            elif stage == 3:
                add(
                    "execution",
                    f"Counter {name} and narrow terms — {opp.title}",
                    detail=(
                        "Counter-offer stage — anchor price at the midpoint and close "
                        "open terms (incoterms, payment instrument, inspection)."
                    ),
                    priority="medium",
                    opportunity_id=opp.id,
                    supplier_lead_id=s.id,
                    due_offset=3,
                )

        if opp.status == "matched":
            add(
                "execution",
                f"Promote match to a Deal & issue SPA — {opp.title}",
                detail=(
                    "Opportunity is matched — formalize the supplier x buyer pair into "
                    "a Deal and circulate the SPA + IMFPA for signature."
                ),
                priority="high",
                opportunity_id=opp.id,
                due_offset=1,
            )

    # 3. Standing execution nudge if any opportunity is negotiating/matched.
    hot = [o for o in opportunities if o.status in ("negotiating", "matched")]
    if hot:
        add(
            "execution",
            f"Advance {len(hot)} live negotiation{'s' if len(hot) > 1 else ''} "
            f"toward SPA/close",
            detail=(
                "Review each live opportunity's negotiation stage; issue the next "
                "stage-appropriate email and request the next commitment (SCO, "
                "NCNDA, bank reference, draft SPA)."
            ),
            priority="high",
            due_offset=2,
        )

    for t in tasks:
        db.add(t)
    await db.commit()
    for t in tasks:
        await db.refresh(t)
    return tasks


# --- Board ----------------------------------------------------------------


async def _pillar_actuals(
    db: AsyncSession, strategy: Strategy
) -> dict[str, float]:
    opportunities = await _relevant_opportunities(db, strategy)
    active_opps = len(opportunities)
    supplier_count = 0
    buyer_count = 0
    execution_count = 0
    for opp in opportunities:
        sup, buy = await _load_leads(db, opp.id)
        supplier_count += sum(1 for s in sup if s.status not in _DEAD_STATUSES)
        buyer_count += sum(1 for b in buy if b.status not in _DEAD_STATUSES)
        if opp.status in ("negotiating", "matched"):
            execution_count += 1
        execution_count += sum(
            1
            for s in sup
            if (s.negotiation_stage or 1) >= 4 and s.status not in _DEAD_STATUSES
        )
    return {
        "origination": float(active_opps),
        "demand": float(buyer_count),
        "supply": float(supplier_count),
        "execution": float(execution_count),
    }


def _pillar_status(progress_pct: float, actual: float) -> str:
    if actual == 0:
        return "idle"
    if progress_pct >= 100:
        return "on_track"
    if progress_pct >= 60:
        return "at_risk"
    return "behind"


async def build_pillar_progress(
    db: AsyncSession, strategy: Strategy, week_start: date
) -> list[PillarProgress]:
    actuals = await _pillar_actuals(db, strategy)
    pillars_cfg = strategy.pillars or _fallback_pillars(strategy)

    week_tasks = (
        await db.execute(
            select(StrategyTask).where(
                StrategyTask.strategy_id == strategy.id,
                StrategyTask.week_start == week_start,
            )
        )
    ).scalars().all()

    out: list[PillarProgress] = []
    for pillar in PILLARS:
        cfg = pillars_cfg.get(pillar) or {}
        target = float(_coerce_int(cfg.get("target"), _DEFAULT_TARGETS[pillar]))
        actual = actuals.get(pillar, 0.0)
        progress = min(100.0, (actual / target * 100.0) if target else 0.0)
        p_tasks = [t for t in week_tasks if t.pillar == pillar]
        done = sum(1 for t in p_tasks if t.status in ("done", "skipped"))
        out.append(
            PillarProgress(
                pillar=pillar,
                label=PILLAR_LABELS[pillar],
                objective=cfg.get("objective"),
                kpi=cfg.get("kpi"),
                target=target,
                actual=actual,
                progress_pct=round(progress, 1),
                tasks_total=len(p_tasks),
                tasks_done=done,
                status=_pillar_status(progress, actual),
                detail=(
                    f"{actual:.0f}/{target:.0f} {cfg.get('kpi') or 'units'} · "
                    f"{done}/{len(p_tasks)} tasks done this week"
                ),
            )
        )
    return out


def select_today_tasks(tasks: list[StrategyTask]) -> list[StrategyTask]:
    today = datetime.now(UTC).date()
    todo = [t for t in tasks if t.status in ("todo", "doing")]

    def is_today(t: StrategyTask) -> bool:
        if t.due_at is None:
            return t.priority == "high"
        return t.due_at.date() <= today or t.priority == "high"

    picked = [t for t in todo if is_today(t)]
    if not picked:
        picked = todo
    order = {"high": 0, "medium": 1, "low": 2}
    picked.sort(key=lambda t: (order.get(t.priority, 3), t.due_at or datetime.max.replace(tzinfo=UTC)))
    return picked[:8]


def compose_headline(pillars: list[PillarProgress]) -> str:
    """One-line summary of where the week stands, shared by board + digest."""
    behind = [p for p in pillars if p.status in ("behind", "idle")]
    if behind:
        return (
            "Focus this week: "
            + ", ".join(p.label for p in behind[:3])
            + " need attention."
        )
    return "All four pillars on track — keep executing the cadence."


# --- Weekly digest --------------------------------------------------------


def build_digest(
    strategy: Strategy,
    week_start: date,
    pillars: list[PillarProgress],
    today_tasks: list[StrategyTask],
    week_tasks: list[StrategyTask],
) -> tuple[str, str]:
    """Render this week's plan as an email (subject, plain-text body)."""
    subject = f"[Atlas] Weekly plan — {strategy.title} (week of {week_start})"

    lines: list[str] = []
    if strategy.north_star:
        lines += [f"North star: {strategy.north_star}", ""]
    lines += [compose_headline(pillars), ""]

    lines.append("PILLAR PROGRESS")
    for p in pillars:
        lines.append(
            f"  - {p.label}: {p.actual:.0f}/{p.target:.0f} {p.kpi or 'units'} · "
            f"{p.tasks_done}/{p.tasks_total} tasks · {p.status.replace('_', ' ')}"
        )
    lines.append("")

    lines.append("TODAY'S FOCUS")
    if today_tasks:
        for t in today_tasks:
            lines.append(f"  - [{t.priority}] {t.title}")
    else:
        lines.append("  - (nothing queued today)")
    lines.append("")

    lines.append("THIS WEEK BY PILLAR")
    for pillar in PILLARS:
        p_tasks = [t for t in week_tasks if t.pillar == pillar]
        if not p_tasks:
            continue
        lines.append(f"{PILLAR_LABELS[pillar]}:")
        for t in p_tasks:
            mark = "x" if t.status in ("done", "skipped") else " "
            lines.append(f"  [{mark}] {t.title}")
        lines.append("")

    body = "\n".join(lines).strip() + "\n"
    return subject, body


# --- Per-task email execution --------------------------------------------


def _fmt_money(value: float | None, currency: str = "USD") -> str:
    return f"{currency} {value:,.0f}/MT" if value else "your best price"


def _opp_context_line(opp: Opportunity | None) -> str:
    if opp is None:
        return ""
    bits: list[str] = [opp.commodity]
    if opp.volume_mt:
        bits.append(f"{opp.volume_mt:,.0f} MT")
    if opp.destination_country:
        bits.append(f"delivered {opp.destination_country}")
    if opp.incoterms:
        bits.append(opp.incoterms)
    return ", ".join(b for b in bits if b)


def _opp_spec_line(opp: Opportunity | None) -> str:
    """The opportunity specifics (volume / destination / incoterms) *without* the
    commodity — for use right after the commodity name so it isn't repeated."""
    if opp is None:
        return ""
    bits: list[str] = []
    if opp.volume_mt:
        bits.append(f"{opp.volume_mt:,.0f} MT")
    if opp.destination_country:
        bits.append(f"delivered {opp.destination_country}")
    if opp.incoterms:
        bits.append(opp.incoterms)
    return ", ".join(bits)


def _supplier_email_template(
    strategy: Strategy,
    opp: Opportunity | None,
    lead: SupplierLead,
    from_name: str,
) -> tuple[str, str]:
    name = lead.contact_name or lead.supplier_name or "there"
    company = lead.supplier_name or "your company"
    commodity = (opp.commodity if opp else None) or strategy.commodity or "the commodity"
    ctx = _opp_context_line(opp)
    stage = lead.negotiation_stage or 1
    dest = (opp.destination_country if opp else None) or strategy.destination_region

    if stage <= 1:
        subject = f"{commodity} enquiry — supply from {company}"
        body = (
            f"Dear {name},\n\n"
            f"We are actively sourcing {commodity} for a confirmed programme"
            f"{f' ({ctx})' if ctx else ''}. Your firm came up as a credible origin "
            f"supplier and I'd like to open a dialogue.\n\n"
            "To move quickly, could you share:\n"
            "  1. A soft corporate offer (SCO) with your FOB/CFR price per MT\n"
            "  2. Available monthly volume and minimum order quantity\n"
            "  3. Payment terms and preferred incoterms\n"
            "  4. Origin, specs and earliest loading window\n\n"
            "We work on an NCNDA basis and can move to SPA quickly once terms line "
            "up. Looking forward to your offer.\n\n"
            f"Best regards,\n{from_name}"
        )
    elif stage == 2:
        subject = f"Re: {commodity} offer — next steps"
        body = (
            f"Dear {name},\n\n"
            "Thank you for the offer — it's under review against our target. Before "
            "we counter, please confirm:\n"
            "  1. Firm validity of the quoted price and volume\n"
            "  2. Payment instrument you can work with (LC at sight / DLC)\n"
            "  3. Inspection (SGS/Intertek) at loading and who bears cost\n"
            "  4. Proof of product / recent SGS certificate\n\n"
            "Once confirmed we'll come back with a firm counter and proceed to "
            "NCNDA + draft SPA.\n\n"
            f"Best regards,\n{from_name}"
        )
    elif stage == 3:
        target = _fmt_money(
            (opp.target_price_max if opp else None) or lead.price_mt,
            (opp.currency if opp else None) or "USD",
        )
        subject = f"Re: {commodity} — our counter"
        body = (
            f"Dear {name},\n\n"
            "Thanks for working through the terms. To close the gap we can commit at "
            f"{target} on the volume discussed, subject to the following:\n"
            "  1. Incoterms and delivery window fixed in the SPA\n"
            "  2. Payment via LC at sight against shipping documents\n"
            "  3. SGS inspection at load port, split 50/50\n\n"
            "If that works in principle, we'll circulate the draft SPA today so we "
            "can lock this in this week.\n\n"
            f"Best regards,\n{from_name}"
        )
    else:
        subject = f"{commodity} — draft SPA & LC terms to close"
        body = (
            f"Dear {name},\n\n"
            "We're aligned on commercials — let's close. Next steps from our side:\n"
            "  1. We circulate the draft SPA for your review\n"
            "  2. We agree the LC terms and issuing bank\n"
            f"  3. We lock the delivery window{f' to {dest}' if dest else ''}\n\n"
            "Please confirm the signatory and banking coordinates so we can finalise "
            "the SPA and open the instrument without delay.\n\n"
            f"Best regards,\n{from_name}"
        )
    return subject, body


def _buyer_email_template(
    strategy: Strategy,
    opp: Opportunity | None,
    lead: BuyerLead,
    from_name: str,
) -> tuple[str, str]:
    name = lead.buyer_name or "there"
    commodity = (opp.commodity if opp else None) or strategy.commodity or "the commodity"
    ctx = _opp_context_line(opp)
    status = lead.status

    if status in ("new",):
        subject = f"{commodity} supply — off-take opportunity"
        body = (
            f"Dear {name},\n\n"
            f"We have secured/are securing competitive {commodity} supply"
            f"{f' ({ctx})' if ctx else ''} and are lining up committed off-take.\n\n"
            "To see if there's a fit, could you share:\n"
            "  1. Your current appetite (volume per month)\n"
            "  2. Target landed price per MT\n"
            "  3. Delivery point and preferred incoterms\n\n"
            "If the numbers work we can move to a firm offer and LOI quickly.\n\n"
            f"Best regards,\n{from_name}"
        )
    elif status == "contacted":
        subject = f"Re: {commodity} supply — following up"
        body = (
            f"Dear {name},\n\n"
            "Just circling back on the "
            f"{commodity} programme. Are you able to share a firm bid and your "
            "target price band? If it lines up with our origin cost we can lock a "
            "volume for you and issue a firm offer.\n\n"
            f"Best regards,\n{from_name}"
        )
    else:  # engaged / quoted / other active
        target = _fmt_money(
            lead.target_price_mt, (opp.currency if opp else None) or "USD"
        )
        subject = f"{commodity} — firm offer & LOI to commit"
        body = (
            f"Dear {name},\n\n"
            "Thanks for the continued interest. We're ready to firm this up. Based on "
            f"our discussion we can offer at around {target}. To reserve the volume "
            "against supply, please send:\n"
            "  1. A firm bid / LOI for the volume\n"
            "  2. Your banking reference for the LC\n\n"
            "On receipt we'll issue the commercial offer and proceed to contract.\n\n"
            f"Best regards,\n{from_name}"
        )
    return subject, body


def is_sourcing_task(task: StrategyTask) -> bool:
    """A supply-pillar task with no linked supplier lead — i.e. "find more
    suppliers". These are executed by running AI Discover, ranking candidates
    and firing an RFQ at each, rather than emailing a single known counterparty.
    """
    return task.pillar == "supply" and task.supplier_lead_id is None


def _supplier_rfq_template(
    strategy: Strategy,
    opp: Opportunity | None,
    supplier_name: str,
    from_name: str,
) -> tuple[str, str]:
    """A ready-to-send RFQ / enquiry to a *prospective* supplier surfaced by
    discovery (we don't have a relationship yet, so this opens the dialogue).
    """
    name = supplier_name or "there"
    commodity = (opp.commodity if opp else None) or strategy.commodity or "the commodity"
    spec = _opp_spec_line(opp)
    origin = strategy.origin_region
    dest = (opp.destination_country if opp else None) or strategy.destination_region

    # Only add a destination clause if the spec line doesn't already carry one.
    dest_clause = dest if dest and not (opp and opp.destination_country) else None
    subject = f"RFQ — {commodity}" + (f", {spec}" if spec else "")
    body = (
        f"Dear {name},\n\n"
        f"We are a trading desk sourcing {commodity}"
        f"{f' ({spec})' if spec else ''}"
        f"{f' for delivery to {dest_clause}' if dest_clause else ''} against a confirmed "
        "programme, and your firm came up as a credible "
        f"{origin + ' ' if origin else ''}origin supplier.\n\n"
        "To evaluate a fit quickly, could you send a soft corporate offer (SCO) with:\n"
        "  1. Your price per MT and incoterms (FOB / CFR)\n"
        "  2. Available monthly volume and minimum order quantity\n"
        "  3. Origin, product specs and earliest loading window\n"
        "  4. Payment terms (we work LC at sight) and proof of product\n\n"
        "We operate on an NCNDA basis and can move to SPA quickly once terms "
        "line up. Looking forward to your offer.\n\n"
        f"Best regards,\n{from_name}"
    )
    return subject, body


def _generic_outreach_template(
    strategy: Strategy,
    opp: Opportunity | None,
    task: StrategyTask,
    from_name: str,
) -> tuple[str, str]:
    """A real, sendable outreach for a task with no linked counterparty — keyed
    to the pillar so it *executes* the task rather than describing it.

    The recipient is left blank for the user to fill (there's no lead to resolve).
    """
    commodity = (opp.commodity if opp else None) or strategy.commodity or "the commodity"
    spec = _opp_spec_line(opp)
    origin = strategy.origin_region
    dest = (opp.destination_country if opp else None) or strategy.destination_region
    dest_clause = dest if dest and not (opp and opp.destination_country) else None

    if task.pillar == "demand":
        subject = f"{commodity} supply — off-take opportunity"
        body = (
            "Dear buyer,\n\n"
            f"We have secured/are securing competitive {commodity} supply"
            f"{f' ({spec})' if spec else ''}"
            f"{f' into {dest_clause}' if dest_clause else ''} and are lining up committed "
            "off-take.\n\n"
            "To see if there's a fit, could you share:\n"
            "  1. Your current appetite (volume per month)\n"
            "  2. Target landed price per MT\n"
            "  3. Delivery point and preferred incoterms\n\n"
            "If the numbers work we can move to a firm offer and LOI quickly.\n\n"
            f"Best regards,\n{from_name}"
        )
    else:  # origination / execution / anything without a counterparty
        lane = " ".join(
            b for b in [origin, "to" if origin and dest else None, dest] if b
        )
        subject = f"{commodity} programme" + (f" — {lane}" if lane else "")
        body = (
            "Dear partner,\n\n"
            "We run an active commodity trading desk and are building flow in "
            f"{commodity}"
            f"{f' on the {lane} lane' if lane else ''}"
            f"{f' ({spec})' if spec else ''}. We're looking to originate new "
            "supply and off-take partners.\n\n"
            "If this is relevant to you, could you share where you sit in the "
            "chain (origin supply, off-take demand, or intermediary), your "
            "typical volumes, and indicative pricing? We can then frame a "
            "concrete trade and move quickly on NCNDA terms.\n\n"
            f"Best regards,\n{from_name}"
        )
    return subject, body


async def draft_task_email(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    *,
    user_id: int | None = None,
) -> dict:
    """Draft a review-ready email for a single strategy task.

    Resolves the recipient from the task's linked supplier/buyer lead and builds
    a stage-aware subject + body. Returns a dict shaped for ``TaskEmailDraft``.
    """
    client = await email_service.resolve_provider(db, user_id)
    from_name = get_settings().gmail_from_name or "The Atlas Trade Desk"

    opp = (
        await db.get(Opportunity, task.opportunity_id)
        if task.opportunity_id is not None
        else None
    )

    supplier = (
        await db.get(SupplierLead, task.supplier_lead_id)
        if task.supplier_lead_id is not None
        else None
    )
    buyer = (
        await db.get(BuyerLead, task.buyer_lead_id)
        if task.buyer_lead_id is not None
        else None
    )

    to_email: str | None = None
    to_name: str | None = None
    reason: str | None = None

    if supplier is not None:
        to_name = supplier.contact_name or supplier.supplier_name
        to_email = supplier.email
        subject, body = _supplier_email_template(strategy, opp, supplier, from_name)
        if not to_email:
            reason = "This supplier lead has no email address — add one to send."
    elif buyer is not None:
        to_name = buyer.buyer_name
        to_email = buyer.email
        subject, body = _buyer_email_template(strategy, opp, buyer, from_name)
        if not to_email:
            reason = "This buyer lead has no email address — add one to send."
    elif is_sourcing_task(task):
        # A "source more suppliers" task: draft a generic RFQ, but nudge the
        # user toward the discovery flow that finds + ranks real candidates.
        subject, body = _supplier_rfq_template(strategy, opp, "there", from_name)
        reason = (
            "Use “Find suppliers” to search, rank and RFQ real candidates — "
            "or add a recipient to send this enquiry directly."
        )
    else:
        # No counterparty linked — draft a real outreach that executes the task
        # (keyed to the pillar), leaving the recipient for the user to fill.
        subject, body = _generic_outreach_template(strategy, opp, task, from_name)
        reason = "This task has no linked counterparty — add a recipient to send."

    return {
        "task_id": task.id,
        "to_email": to_email,
        "to_name": to_name,
        "subject": subject,
        "body": body,
        "opportunity_id": task.opportunity_id,
        "supplier_lead_id": task.supplier_lead_id,
        "buyer_lead_id": task.buyer_lead_id,
        "mode": "live" if client.configured else "offline",
        "can_send": bool(to_email),
        "reason": reason,
    }


async def send_task_email(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    *,
    to_email: str,
    subject: str,
    body: str,
    complete_task: bool = True,
    user_id: int | None = None,
) -> tuple[EmailMessage, StrategyTask, str]:
    """Send (or offline-record) a task's email and tick the task off.

    Links the message to the task's opportunity/lead, and — when the send
    succeeds and ``complete_task`` is set — marks the task ``done``.
    Returns ``(email_message, task, mode)``.
    """
    client = await email_service.resolve_provider(db, user_id)
    mode = "live" if client.configured else "offline"

    msg = await email_service.send_email(
        db,
        to_email=to_email,
        subject=subject,
        body=body,
        user_id=user_id,
        opportunity_id=task.opportunity_id,
        supplier_lead_id=task.supplier_lead_id,
        buyer_lead_id=task.buyer_lead_id,
        client=client,
    )

    if complete_task and msg.status in ("sent", "offline"):
        task.status = "done"
        task.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(task)

    return msg, task, mode


def _rank_candidates(
    candidates: list[SupplierCandidate], cp: CounterpartyService
) -> list[tuple[SupplierCandidate, dict]]:
    """Score every candidate and rank by credibility (desc), then risk (asc),
    preferring those we already have a contact email for.
    """
    scored: list[tuple[SupplierCandidate, dict]] = []
    for c in candidates:
        probe = Supplier(
            name=c.name,
            type=c.type,
            country=c.country,
            commodity=c.commodity,
            website=c.website,
            email=c.email,
            description=c.description,
        )
        scored.append((c, cp.score(probe)))
    scored.sort(
        key=lambda t: (
            -t[1]["credibility_score"],
            t[1]["risk_score"],
            0 if t[0].email else 1,
        )
    )
    return scored


async def source_suppliers_for_task(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    *,
    limit: int = 4,
    user_id: int | None = None,
) -> dict:
    """Execute a "source more suppliers" task: run AI Discover for the
    commodity/lane, qualify + rank the candidates and return the top ``limit``
    each with a ready-to-send RFQ draft. Shaped for ``SourcingResult``.
    """
    client = await email_service.resolve_provider(db, user_id)
    from_name = get_settings().gmail_from_name or "The Atlas Trade Desk"

    opp = (
        await db.get(Opportunity, task.opportunity_id)
        if task.opportunity_id is not None
        else None
    )
    commodity = (opp.commodity if opp else None) or strategy.commodity or "sugar"
    country = strategy.origin_region

    service = SupplierDiscoveryService(db)
    found = await service.discover(
        commodity=commodity, country=country, limit=max(limit * 3, 12)
    )
    cp = CounterpartyService()
    ranked = _rank_candidates(found, cp)[:limit]

    candidates: list[dict] = []
    for c, sc in ranked:
        subject, body = _supplier_rfq_template(strategy, opp, c.name, from_name)
        candidates.append(
            {
                "name": c.name,
                "country": c.country,
                "website": c.website,
                "email": c.email,
                "phone": c.phone,
                "contact_name": c.contact_name,
                "type": c.type,
                "source": c.source,
                "description": c.description,
                "credibility_score": sc["credibility_score"],
                "risk_score": sc["risk_score"],
                "red_flags": sc["red_flags"],
                "subject": subject,
                "body": body,
                "can_send": bool(c.email),
                "reason": (
                    None
                    if c.email
                    else "No public email found — add a recipient to send."
                ),
            }
        )

    return {
        "task_id": task.id,
        "opportunity_id": task.opportunity_id,
        "commodity": commodity,
        "country": country,
        "mode": "live" if client.configured else "offline",
        "candidates": candidates,
    }


async def send_sourcing_email(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    *,
    to_email: str,
    subject: str,
    body: str,
    supplier_name: str,
    country: str | None = None,
    website: str | None = None,
    contact_name: str | None = None,
    complete_task: bool = False,
    user_id: int | None = None,
) -> tuple[EmailMessage, StrategyTask, str, SupplierLead | None]:
    """Send (or offline-record) an RFQ to a discovered supplier candidate.

    When the task is tied to an opportunity, a tracked ``SupplierLead`` is
    created so the RFQ shows in the pipeline and future replies match back to
    it. Ticks the task off only when ``complete_task`` is set (a sourcing task
    typically fans out to several candidates).
    """
    client = await email_service.resolve_provider(db, user_id)
    mode = "live" if client.configured else "offline"

    lead: SupplierLead | None = None
    if task.opportunity_id is not None:
        opp = await db.get(Opportunity, task.opportunity_id)
        commodity = (opp.commodity if opp else None) or strategy.commodity
        cp = CounterpartyService()
        probe = Supplier(
            name=supplier_name,
            country=country,
            commodity=commodity,
            website=website,
            email=to_email,
        )
        sc = cp.score(probe)
        lead = SupplierLead(
            opportunity_id=task.opportunity_id,
            supplier_name=supplier_name,
            country=country,
            email=to_email,
            contact_name=contact_name,
            credibility_score=sc["credibility_score"],
            status="new",
            notes=f"Sourced via AI Discover for “{task.title}”.",
        )
        db.add(lead)
        await db.flush()

    msg = await email_service.send_email(
        db,
        to_email=to_email,
        subject=subject,
        body=body,
        user_id=user_id,
        opportunity_id=task.opportunity_id,
        supplier_lead_id=lead.id if lead is not None else None,
        client=client,
    )

    if complete_task and msg.status in ("sent", "offline"):
        task.status = "done"
        task.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(task)
    elif lead is not None:
        await db.refresh(lead)

    return msg, task, mode, lead


async def send_weekly_digest(
    db: AsyncSession,
    strategy: Strategy,
    *,
    to_email: str,
    week_start: date | None = None,
    user_id: int | None = None,
) -> tuple[EmailMessage, str, str]:
    """Compose the current week's plan and send (or offline-record) it.

    Returns ``(email_message, subject, mode)`` where ``mode`` is ``"live"`` or
    ``"offline"`` depending on whether Gmail credentials are configured.
    """
    week = _monday(week_start or datetime.now(UTC).date())
    pillars = await build_pillar_progress(db, strategy, week)
    week_tasks = (
        await db.execute(
            select(StrategyTask)
            .where(
                StrategyTask.strategy_id == strategy.id,
                StrategyTask.week_start == week,
            )
            .order_by(StrategyTask.priority.asc(), StrategyTask.id.asc())
        )
    ).scalars().all()
    today_tasks = select_today_tasks(list(week_tasks))

    subject, body = build_digest(strategy, week, pillars, today_tasks, list(week_tasks))

    client = await email_service.resolve_provider(db, user_id)
    mode = "live" if client.configured else "offline"
    msg = await email_service.send_email(
        db,
        to_email=to_email,
        subject=subject,
        body=body,
        user_id=user_id,
        client=client,
    )
    return msg, subject, mode
