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
from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.models.strategy import (
    PILLAR_LABELS,
    PILLARS,
    Strategy,
    StrategyTask,
)
from app.schemas.strategy import PillarProgress
from app.services import matching as matching_service
from app.services import next_action as next_action_service

logger = logging.getLogger("atlas.strategy")

# Default pillar coverage targets when the user hasn't set their own.
_DEFAULT_TARGETS = {
    "origination": 3,   # active opportunities in flight
    "demand": 3,        # active buyer leads across opportunities
    "supply": 5,        # active supplier leads across opportunities
    "execution": 1,     # matches promoted toward a deal
}

_DEAD_STATUSES = ("declined", "lost")


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

    # 2. Per-opportunity next-actions, classified into pillars.
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
