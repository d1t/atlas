"""Turning planned capabilities into real, gated work.

Planning and execution are deliberately separate. :mod:`app.agents.orchestrator`
decides *what* should happen and can never cause an external effect; this module is the
only place an agent reaches the outside world, and everything it does passes through
three checks it cannot bypass:

* **Approval.** Outbound mail is judged by :mod:`app.services.approval_policy` on its
  actual content — recipient, commercial language, attachments — not merely its type.
* **Idempotency.** Actions carry a deterministic key, so re-running the executor cannot
  send the same counterparty the same message twice.
* **Evidence.** An action records what it produced. Sending is never treated as an
  outcome: a send parks in ``waiting_response`` and only a reply moves it on.

The handlers reuse the services the product already ships — supplier discovery, ranking,
draft generation, the mailbox provider. An agent that had its own private copy of those
would drift from what a human sees in the UI, and the divergence would be invisible.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import AgentAction, AgentRun, Evidence
from app.models.strategy import Strategy, StrategyTask
from app.services import approval_policy, email_service, execution_service, strategy_service

logger = logging.getLogger(__name__)

EXECUTOR_KEY = "executor"

#: Capability -> the action type recorded against it. The distinction matters: the
#: policy gates action types, and ``draft_email`` has no external effect while
#: ``send_email`` does.
ACTION_TYPE_FOR = {
    "research_suppliers": "research",
    "draft_email": "draft_email",
    "send_email": "send_email",
}


class AgentsPaused(RuntimeError):
    """The user has stopped every agent on this strategy."""


class CapabilityFailed(RuntimeError):
    """A handler could not do its job. Carries a message fit to show a user."""

    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        #: ``True`` when the obstacle is a missing input a human must supply, which is a
        #: blocker rather than a failure worth retrying.
        self.blocked = blocked


@dataclass
class Outcome:
    """What running one action produced."""

    action_id: int
    task_id: int | None
    capability: str
    state: str
    detail: str = ""


@dataclass
class ExecutionReport:
    run_id: int | None = None
    outcomes: list[Outcome] = field(default_factory=list)

    def by_state(self, state: str) -> list[Outcome]:
        return [o for o in self.outcomes if o.state == state]

    @property
    def summary(self) -> str:
        if not self.outcomes:
            return "Nothing to execute."
        counts: dict[str, int] = {}
        for o in self.outcomes:
            counts[o.state] = counts.get(o.state, 0) + 1
        parts = ", ".join(f"{n} {state}" for state, n in sorted(counts.items()))
        return f"{len(self.outcomes)} action(s): {parts}."


# --- Selecting what is runnable ---------------------------------------------


async def _ready_tasks(
    db: AsyncSession, strategy: Strategy, *, limit: int
) -> list[StrategyTask]:
    """Agent-owned tasks with a capability, no action yet, and dependencies met."""
    tasks = (
        (
            await db.execute(
                select(StrategyTask)
                .where(
                    StrategyTask.strategy_id == strategy.id,
                    StrategyTask.assignee == "agent",
                    StrategyTask.capability.is_not(None),
                    StrategyTask.status.in_(("todo", "in_progress")),
                )
                .order_by(StrategyTask.position.asc(), StrategyTask.id.asc())
            )
        )
        .scalars()
        .all()
    )

    ready: list[StrategyTask] = []
    for task in tasks:
        if len(ready) >= limit:
            break
        # An action already exists for this task unless it was abandoned, in which case
        # the task is fair game again.
        existing = (
            (
                await db.execute(
                    select(AgentAction).where(AgentAction.task_id == task.id)
                )
            )
            .scalars()
            .all()
        )
        if any(a.state not in ("cancelled", "rejected", "failed") for a in existing):
            continue
        blockers = await execution_service.blocking_dependencies(db, task)
        if blockers:
            continue
        ready.append(task)
    return ready


async def _queued_actions(
    db: AsyncSession, strategy: Strategy, *, limit: int
) -> list[AgentAction]:
    """Actions cleared to run — typically ones a human has just approved."""
    return list(
        (
            await db.execute(
                select(AgentAction)
                .where(
                    AgentAction.strategy_id == strategy.id,
                    AgentAction.state == "queued",
                )
                .order_by(AgentAction.id.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


# --- Capability handlers ------------------------------------------------------


async def _handle_research_suppliers(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    action: AgentAction,
    *,
    user_id: int | None,
) -> dict:
    """Search, qualify and rank suppliers, then record the shortlist as evidence."""
    data = await strategy_service.source_suppliers_for_task(
        db, strategy, task, limit=4, user_id=user_id
    )
    candidates = data.get("candidates") or []
    if not candidates:
        # An empty search is a real result, not an error — but it must not read as
        # success, or the task would tick off having found nobody.
        raise CapabilityFailed(
            "Supplier discovery returned no credible candidates for "
            f"{data.get('commodity') or strategy.commodity}. Widen the origin or "
            "commodity, or add candidates manually.",
            blocked=True,
        )

    shortlist = [
        {
            "name": c.get("name"),
            "country": c.get("country"),
            "website": c.get("website"),
            "email": c.get("email"),
            "credibility_score": c.get("credibility_score"),
            "risk_score": c.get("risk_score"),
            "red_flags": c.get("red_flags"),
            "contactable": bool(c.get("email")),
        }
        for c in candidates
    ]
    top = ", ".join(
        f"{c['name']} ({c['credibility_score']})" for c in shortlist
    )

    db.add(
        Evidence(
            task_id=task.id,
            kind="research",
            description=(
                f"Discovered and ranked {len(shortlist)} supplier candidate(s): {top}."
            ),
            opportunity_id=task.opportunity_id,
            payload={"candidates": shortlist},
            created_by_type="agent",
        )
    )
    await db.flush()
    return {"candidates": shortlist, "count": len(shortlist)}


async def _draft_for(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    *,
    user_id: int | None,
) -> dict:
    draft = await strategy_service.draft_task_email(
        db, strategy, task, user_id=user_id
    )
    if not draft.get("to_email"):
        raise CapabilityFailed(
            draft.get("reason")
            or "No recipient could be resolved for this task, so nothing can be sent.",
            blocked=True,
        )
    return draft


async def _handle_draft_email(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    action: AgentAction,
    *,
    user_id: int | None,
) -> dict:
    """Produce a ready-to-send message and hand it to a human.

    A draft is not an outcome, so this never completes the task — it parks for review.
    """
    draft = await _draft_for(db, strategy, task, user_id=user_id)
    return {
        "to_email": draft["to_email"],
        "to_name": draft.get("to_name"),
        "subject": draft["subject"],
        "body": draft["body"],
    }


async def _handle_send_email(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    action: AgentAction,
    *,
    user_id: int | None,
) -> dict:
    """Send an already-approved message and park awaiting a reply."""
    payload = action.payload or {}
    to_email = payload.get("to_email")
    subject = payload.get("subject") or ""
    body = payload.get("body") or ""
    if not to_email:
        raise CapabilityFailed(
            "This action has no recipient, so it cannot be sent.", blocked=True
        )

    message = await email_service.send_email(
        db,
        to_email=to_email,
        subject=subject,
        body=body,
        user_id=user_id,
        opportunity_id=task.opportunity_id,
        supplier_lead_id=task.supplier_lead_id,
        buyer_lead_id=task.buyer_lead_id,
    )
    if message.status == "failed":
        raise CapabilityFailed(
            message.error or "The mailbox rejected the message."
        )

    action.email_message_id = message.id
    if grant_id := (payload.get("grant_id")):
        # Only count a message that actually went out against the user's allowance.
        await approval_policy.consume_grant(db, grant_id)

    db.add(
        Evidence(
            task_id=task.id,
            kind="email",
            description=f"Sent “{subject}” to {to_email}.",
            email_message_id=message.id,
            opportunity_id=task.opportunity_id,
            payload={"status": message.status},
            created_by_type="agent",
        )
    )
    await db.flush()
    return {
        "email_message_id": message.id,
        "status": message.status,
        "to_email": to_email,
    }


#: Handlers, and the state an action rests in once each succeeds. ``send_email`` stops
#: at ``waiting_response`` because a delivered message proves nothing about the outcome.
HANDLERS = {
    "research_suppliers": (_handle_research_suppliers, "completed"),
    "draft_email": (_handle_draft_email, "waiting_human"),
    "send_email": (_handle_send_email, "waiting_response"),
}


# --- Proposing and running ----------------------------------------------------


async def _propose_for_task(
    db: AsyncSession,
    strategy: Strategy,
    task: StrategyTask,
    *,
    run: AgentRun | None,
    user_id: int | None,
) -> AgentAction | None:
    """Create the action a task's capability calls for, gated as policy requires."""
    capability = task.capability
    action_type = ACTION_TYPE_FOR.get(capability or "", capability or "")

    payload: dict = {}
    decision = None
    target = str(task.id)

    if capability == "send_email":
        # The draft is built *before* the gate so a reviewer approves the actual words,
        # not an intention to write some.
        draft = await _draft_for(db, strategy, task, user_id=user_id)
        payload = {
            "to_email": draft["to_email"],
            "to_name": draft.get("to_name"),
            "subject": draft["subject"],
            "body": draft["body"],
        }
        target = str(draft["to_email"]).lower()
        decision = await approval_policy.evaluate(
            db,
            strategy_id=strategy.id,
            action_type="send_email",
            email=approval_policy.EmailContext(
                recipient=draft["to_email"],
                subject=draft["subject"],
                body=draft["body"],
                thread_key=f"task:{task.id}",
                template_key=f"{task.pillar}:{capability}",
            ),
        )
        payload["grant_id"] = decision.grant_id
        payload["policy"] = decision.reason

    try:
        return await execution_service.propose_action(
            db,
            strategy_id=strategy.id,
            action_type=action_type,
            target=target,
            run=run,
            task_id=task.id,
            payload=payload,
            rationale=task.detail or task.title,
            decision=decision,
        )
    except execution_service.DuplicateAction:
        # Another run already owns this outreach. Silence here is the correct
        # behaviour — the duplicate guard exists precisely to be hit.
        logger.info("Skipping duplicate %s for task %s", action_type, task.id)
        return None


async def run_action(
    db: AsyncSession,
    action: AgentAction,
    *,
    user_id: int | None = None,
) -> Outcome:
    """Execute one queued action, recording success, blockage or failure.

    The pause is re-checked here and not only in :func:`execute`, because an approval
    granted before the brake went on must not carry an action past it.
    """
    task = await db.get(StrategyTask, action.task_id) if action.task_id else None
    strategy = await db.get(Strategy, action.strategy_id)
    if strategy is not None and strategy.agents_paused:
        raise AgentsPaused(
            strategy.agents_paused_reason
            or "Agents are paused for this strategy. Resume them to continue."
        )
    capability = next(
        (c for c, t in ACTION_TYPE_FOR.items() if t == action.action_type),
        None,
    )
    if task is None or strategy is None or capability not in HANDLERS:
        await execution_service.transition(
            db,
            action,
            "failed",
            error=f"No handler for '{action.action_type}'.",
        )
        return Outcome(
            action.id, action.task_id, capability or action.action_type, "failed",
            "No handler.",
        )

    handler, resting_state = HANDLERS[capability]
    await execution_service.transition(db, action, "in_progress")
    action.attempts += 1
    action.started_at = datetime.now(UTC)

    try:
        result = await handler(db, strategy, task, action, user_id=user_id)
    except CapabilityFailed as exc:
        state = "blocked" if exc.blocked else "failed"
        await execution_service.transition(db, action, state, error=str(exc))
        task.blocked_reason = str(exc)
        await db.flush()
        return Outcome(action.id, task.id, capability, state, str(exc))
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        logger.exception("Capability %s failed", capability)
        await execution_service.transition(db, action, "failed", error=str(exc))
        await db.flush()
        return Outcome(action.id, task.id, capability, "failed", str(exc))

    await execution_service.transition(db, action, resting_state, result=result)
    if resting_state == "completed":
        action.completed_at = datetime.now(UTC)
    task.blocked_reason = None
    if task.status == "todo":
        task.status = "in_progress"

    # Only a capability that genuinely finishes its task may close it. Evidence still
    # has to satisfy the gate, so this cannot tick off an unproven outcome.
    if resting_state == "completed":
        try:
            await execution_service.complete_task(
                db, task, actor_type="agent", actor_label=EXECUTOR_KEY
            )
        except execution_service.EvidenceRequired as exc:
            logger.info("Task %s left open: %s", task.id, exc)

    await db.flush()
    return Outcome(action.id, task.id, capability, resting_state)


async def execute(
    db: AsyncSession,
    strategy: Strategy,
    *,
    user_id: int | None = None,
    limit: int = 20,
) -> ExecutionReport:
    """Propose actions for ready tasks, then run everything cleared to run.

    Safe to call repeatedly: tasks with a live action are skipped, and the idempotency
    key catches anything the task-level check misses.

    Raises :class:`AgentsPaused` when the strategy's brake is on, so a pause takes
    effect on the next attempt rather than at the end of the current one.
    """
    if strategy.agents_paused:
        raise AgentsPaused(
            strategy.agents_paused_reason
            or "Agents are paused for this strategy. Resume them to continue."
        )
    run = AgentRun(
        strategy_id=strategy.id,
        agent_key=EXECUTOR_KEY,
        trigger="manual",
        status="running",
        started_at=datetime.now(UTC),
    )
    db.add(run)
    await db.flush()

    report = ExecutionReport(run_id=run.id)
    try:
        for task in await _ready_tasks(db, strategy, limit=limit):
            try:
                action = await _propose_for_task(
                    db, strategy, task, run=run, user_id=user_id
                )
            except CapabilityFailed as exc:
                # One un-actionable task must not abort the pass: the rest of the
                # strategy's work is still valid.
                task.blocked_reason = str(exc)
                await db.flush()
                report.outcomes.append(
                    Outcome(0, task.id, task.capability or "", "blocked", str(exc))
                )
                continue
            if action is None:
                continue
            if action.state == "awaiting_approval":
                report.outcomes.append(
                    Outcome(
                        action.id,
                        task.id,
                        task.capability or "",
                        "awaiting_approval",
                        (action.payload or {}).get("policy", ""),
                    )
                )

        for action in await _queued_actions(db, strategy, limit=limit):
            action.run_id = action.run_id or run.id
            report.outcomes.append(await run_action(db, action, user_id=user_id))
    except Exception as exc:  # noqa: BLE001 - recorded then re-raised
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.now(UTC)
        await db.flush()
        raise

    run.status = "completed"
    run.finished_at = datetime.now(UTC)
    run.summary = report.summary
    run.reasoning = (
        "Executed the capabilities planned for this strategy. Outbound mail was "
        "gated by the approval policy; nothing was sent that policy did not clear."
    )
    await execution_service.record_audit(
        db,
        strategy_id=strategy.id,
        actor_type="agent",
        actor_label=EXECUTOR_KEY,
        action="executor.ran",
        entity_type="agent_run",
        entity_id=run.id,
        after={
            "actions": len(report.outcomes),
            "states": {o.state: 1 for o in report.outcomes},
        },
    )
    await db.flush()
    return report
