"""The strategy orchestrator.

Builds a real snapshot of the pipeline, asks each pillar agent what its pillar needs,
persists the result as a task tree, and — separately — resumes agents whose work was
parked waiting for someone to reply.

Planning and acting are deliberately separate calls. A planning run never sends
anything; it only produces tasks whose completion is gated on evidence.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, LeadView, TaskSpec
from app.agents.pillars import PILLAR_AGENTS
from app.models.deal import Deal
from app.models.email import EmailMessage
from app.models.execution import AgentAction, AgentRun, Evidence
from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.models.strategy import Strategy, StrategyTask
from app.services import execution_service

logger = logging.getLogger(__name__)

ORCHESTRATOR_KEY = "strategy_orchestrator"


async def build_context(
    db: AsyncSession, strategy: Strategy, *, now: datetime | None = None
) -> AgentContext:
    """Assemble the pipeline snapshot the agents plan against.

    Opportunities are scoped by commodity, which is how a strategy's pipeline is
    identified in this schema; leads follow from those opportunities.
    """
    opp_query = select(Opportunity)
    if strategy.commodity:
        opp_query = opp_query.where(Opportunity.commodity == strategy.commodity)
    opportunities = tuple((await db.execute(opp_query)).scalars().all())
    opp_ids = [o.id for o in opportunities]

    supplier_leads: tuple[LeadView, ...] = ()
    buyer_leads: tuple[LeadView, ...] = ()
    deals: tuple[Deal, ...] = ()

    if opp_ids:
        rows = (
            await db.execute(
                select(SupplierLead).where(SupplierLead.opportunity_id.in_(opp_ids))
            )
        ).scalars().all()
        supplier_leads = tuple(
            LeadView(
                id=r.id,
                name=r.supplier_name or f"Supplier #{r.id}",
                email=r.email,
                status=r.status,
                price_mt=r.price_mt,
                last_contacted_at=r.last_contacted_at,
                opportunity_id=r.opportunity_id,
            )
            for r in rows
        )

        brows = (
            await db.execute(
                select(BuyerLead).where(BuyerLead.opportunity_id.in_(opp_ids))
            )
        ).scalars().all()
        buyer_leads = tuple(
            LeadView(
                id=r.id,
                name=r.buyer_name or f"Buyer #{r.id}",
                email=r.email,
                status=r.status,
                volume_mt=r.volume_mt,
                price_mt=r.target_price_mt,
                last_contacted_at=r.last_contacted_at,
                opportunity_id=r.opportunity_id,
            )
            for r in brows
        )

        deals = tuple(
            (
                await db.execute(
                    select(Deal).where(Deal.opportunity_id.in_(opp_ids))
                )
            ).scalars().all()
        )

    existing = tuple(
        (
            await db.execute(
                select(StrategyTask).where(StrategyTask.strategy_id == strategy.id)
            )
        ).scalars().all()
    )

    return AgentContext(
        strategy=strategy,
        opportunities=opportunities,
        supplier_leads=supplier_leads,
        buyer_leads=buyer_leads,
        deals=deals,
        existing_tasks=existing,
        now=now or datetime.now(UTC),
    )


async def _persist(
    db: AsyncSession,
    strategy: Strategy,
    specs: list[TaskSpec],
    *,
    parent: StrategyTask | None = None,
    position_offset: int = 0,
) -> list[StrategyTask]:
    """Write a spec tree to the database, resolving sibling dependencies to ids."""
    created: list[StrategyTask] = []
    by_title: dict[str, StrategyTask] = {}

    for index, spec in enumerate(specs):
        task = StrategyTask(
            strategy_id=strategy.id,
            parent_id=parent.id if parent is not None else None,
            pillar=spec.pillar,
            kind=spec.kind,
            title=spec.title,
            detail=spec.detail,
            priority=spec.priority,
            status="todo",
            source="agent",
            position=position_offset + index,
            assignee=spec.assignee,
            agent_key=spec.agent_key,
            confidence=spec.confidence,
            acceptance_criteria=spec.acceptance_criteria,
            requires_evidence=spec.requires_evidence,
            opportunity_id=spec.opportunity_id,
            supplier_lead_id=spec.supplier_lead_id,
            buyer_lead_id=spec.buyer_lead_id,
        )
        db.add(task)
        await db.flush()
        created.append(task)
        by_title[spec.title.strip().lower()] = task

        if spec.children:
            created.extend(await _persist(db, strategy, spec.children, parent=task))

    # Second pass: dependencies can only be resolved once siblings have ids.
    for spec in specs:
        task = by_title[spec.title.strip().lower()]
        deps = [
            by_title[t.strip().lower()].id
            for t in spec.depends_on_titles
            if t.strip().lower() in by_title
        ]
        if deps:
            task.depends_on_ids = deps
    await db.flush()
    return created


async def plan(
    db: AsyncSession,
    strategy: Strategy,
    *,
    now: datetime | None = None,
) -> tuple[AgentRun, list[StrategyTask]]:
    """Decompose the strategy's outstanding gaps into a task tree.

    Idempotent in the sense that matters: agents skip work already on the board, so a
    second run with an unchanged pipeline creates nothing.
    """
    ctx = await build_context(db, strategy, now=now)

    run = AgentRun(
        strategy_id=strategy.id,
        agent_key=ORCHESTRATOR_KEY,
        trigger="manual",
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()

    created: list[StrategyTask] = []
    summary: dict[str, int] = {}
    try:
        for agent in PILLAR_AGENTS:
            specs = agent.decompose(ctx)
            for spec in specs:
                _assert_capabilities(agent.key, agent.capabilities, spec)
            if not specs:
                summary[agent.key] = 0
                continue
            offset = len([t for t in ctx.existing_tasks if t.pillar == agent.pillar])
            tasks = await _persist(
                db, strategy, specs, position_offset=offset
            )
            created.extend(tasks)
            summary[agent.key] = len(tasks)
    except Exception as exc:  # noqa: BLE001 - recorded then re-raised
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(UTC)
        await db.flush()
        raise

    run.status = "completed"
    run.finished_at = datetime.now(UTC)
    run.summary = (
        f"Created {len(created)} task(s) across "
        f"{len([k for k, v in summary.items() if v])} pillar(s)."
    )
    # The reasoning field is what the UI shows when a user asks "why this work?" —
    # it must cite the figures the plan was derived from, not restate the plan.
    run.reasoning = (
        f"Target {ctx.target_volume_mt:,.0f} MT. "
        f"Buyers committed {ctx.committed_buy_volume_mt:,.0f} MT "
        f"(gap {ctx.demand_gap_mt:,.0f} MT). "
        f"Supply shortlisted {ctx.shortlisted_supply_mt:,.0f} MT "
        f"(gap {ctx.supply_gap_mt:,.0f} MT). "
        f"{len([o for o in ctx.opportunities if o.status not in ('closed', 'lost')])} "
        f"live opportunity(s), {len(ctx.buyer_leads)} buyer lead(s), "
        f"{len(ctx.supplier_leads)} supplier lead(s)."
    )

    await execution_service.record_audit(
        db,
        strategy_id=strategy.id,
        actor_type="agent",
        actor_label=ORCHESTRATOR_KEY,
        action="orchestrator.planned",
        entity_type="agent_run",
        entity_id=run.id,
        after={
            "tasks_created": len(created),
            "by_agent": summary,
            "demand_gap_mt": ctx.demand_gap_mt,
            "supply_gap_mt": ctx.supply_gap_mt,
        },
    )
    await db.flush()
    return run, created


def _assert_capabilities(
    agent_key: str, allowed: tuple[str, ...], spec: TaskSpec
) -> None:
    """An agent may not schedule a capability it was not granted."""
    if spec.capability is not None and spec.capability not in allowed:
        raise ValueError(
            f"{agent_key} attempted to schedule '{spec.capability}', which it is not "
            "permitted to use."
        )
    for child in spec.children:
        _assert_capabilities(agent_key, allowed, child)


# --- Resuming after a reply -------------------------------------------------


async def resume_from_replies(
    db: AsyncSession, strategy: Strategy
) -> list[AgentAction]:
    """Wake actions parked in ``waiting_response`` for which a reply has arrived.

    An agent that sends an email does not sit in a loop waiting. It parks the action in
    ``waiting_response`` and ends its run. When reply sync later stores an inbound
    message on that thread, this brings the action back: it records the reply as
    evidence against the task, then hands over for analysis.

    Without the evidence step the reply would move the state machine without leaving
    anything behind to justify completing the task later.
    """
    waiting = (
        await db.execute(
            select(AgentAction).where(
                AgentAction.strategy_id == strategy.id,
                AgentAction.state == "waiting_response",
            )
        )
    ).scalars().all()
    if not waiting:
        return []

    resumed: list[AgentAction] = []
    for action in waiting:
        if action.email_message_id is None:
            continue
        sent = await db.get(EmailMessage, action.email_message_id)
        if sent is None or not sent.message_id:
            continue

        # Match on the RFC 2822 header the outbound message carried, which is how
        # replies are already stitched to their parent elsewhere in the app.
        reply = (
            await db.execute(
                select(EmailMessage)
                .where(
                    EmailMessage.in_reply_to == sent.message_id,
                    EmailMessage.direction == "inbound",
                )
                .order_by(EmailMessage.id.desc())
            )
        ).scalars().first()
        if reply is None:
            continue

        if action.task_id is not None:
            db.add(
                Evidence(
                    task_id=action.task_id,
                    kind="reply_received",
                    description=(
                        f"Reply from {reply.from_email or 'counterparty'}: "
                        f"{(reply.subject or '').strip()[:120]}"
                    ),
                    email_message_id=reply.id,
                    created_by_type="agent",
                    payload={"action_id": action.id, "resumed_run": True},
                )
            )

        await execution_service.transition(
            db,
            action,
            "in_progress",
            actor_type="agent",
            result={
                "resumed_by": "reply",
                "reply_message_id": reply.id,
                "in_reply_to": sent.message_id,
            },
        )
        resumed.append(action)

    await db.flush()
    return resumed
