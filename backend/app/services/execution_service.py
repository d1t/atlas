"""Execution rules — the invariants that make agent activity trustworthy.

Three things are enforced here rather than left to callers, because each of them is a
place where an agent system quietly starts lying to its user:

1. **Legal state transitions.** An action cannot jump from ``proposed`` to ``completed``.
   Every move is checked against :data:`ALLOWED_TRANSITIONS` and written to the audit log.
2. **Approval before anything external.** Action types in ``ALWAYS_APPROVED_ACTION_TYPES``
   get an :class:`~app.models.execution.Approval` and sit in ``awaiting_approval`` until a
   human decides. Callers cannot opt out.
3. **Evidence before completion.** A task marked ``requires_evidence`` cannot be completed
   without a supporting artefact — sending an email is not the same as winning a buyer.
   A human may still override, but only on the record, with a reason.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import (
    ALWAYS_APPROVED_ACTION_TYPES,
    AUTONOMOUS_ACTION_TYPES,
    AgentAction,
    AgentRun,
    Approval,
    AuditLog,
    Evidence,
    KpiSnapshot,
)
from app.models.strategy import StrategyTask
from app.models.user import User

#: Legal moves through the action lifecycle. Anything absent is rejected, which keeps
#: impossible histories (e.g. rejected -> completed) out of the audit trail entirely.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"awaiting_approval", "queued", "rejected", "cancelled"}),
    "awaiting_approval": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"queued", "cancelled"}),
    "queued": frozenset({"in_progress", "blocked", "cancelled"}),
    "in_progress": frozenset(
        {"completed", "failed", "waiting_response", "waiting_human", "blocked"}
    ),
    "waiting_response": frozenset(
        {"completed", "in_progress", "failed", "blocked", "cancelled"}
    ),
    "waiting_human": frozenset({"in_progress", "blocked", "cancelled"}),
    "blocked": frozenset({"queued", "in_progress", "cancelled"}),
    "failed": frozenset({"retrying", "cancelled"}),
    "retrying": frozenset({"in_progress", "failed", "cancelled"}),
    # Terminal.
    "completed": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}


class TransitionError(ValueError):
    """Raised when a caller attempts an illegal action state change."""


class EvidenceRequired(ValueError):
    """Raised when a task gated on evidence is completed without any."""


class DuplicateAction(ValueError):
    """Raised when an action with the same idempotency key already exists."""


def build_idempotency_key(
    strategy_id: int, action_type: str, target: str
) -> str:
    """Derive the key that stops the same action being fired twice.

    ``target`` should identify *what* the action concerns — a recipient address, a
    lead id, a task id. Two agents independently deciding to email the same supplier
    about the same task therefore collide, and the second one is refused.
    """
    raw = f"{strategy_id}:{action_type}:{target.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def requires_approval_for(action_type: str, *, trusted: bool = False) -> bool:
    """Decide whether an action type needs a human gate.

    ``trusted`` lets a user pre-authorise a low-risk workflow, but it can never
    unlock the always-gated types — sending mail, committing funds, signing, deleting.
    """
    if action_type in ALWAYS_APPROVED_ACTION_TYPES:
        return True
    if action_type in AUTONOMOUS_ACTION_TYPES:
        return False
    # Unknown action types are gated by default: an agent gaining a new capability
    # should not also silently gain permission to use it unsupervised.
    return not trusted


async def record_audit(
    db: AsyncSession,
    *,
    strategy_id: int | None,
    actor_type: str,
    action: str,
    entity_type: str,
    entity_id: int | None,
    actor_id: int | None = None,
    actor_label: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        strategy_id=strategy_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before or {},
        after=after or {},
    )
    db.add(entry)
    return entry


async def propose_action(
    db: AsyncSession,
    *,
    strategy_id: int,
    action_type: str,
    target: str,
    run: AgentRun | None = None,
    task_id: int | None = None,
    payload: dict | None = None,
    rationale: str | None = None,
    risk: str = "medium",
    trusted: bool = False,
) -> AgentAction:
    """Create an action, gating it behind an approval when the type demands one.

    Raises :class:`DuplicateAction` if an equivalent action already exists and has not
    been cancelled or rejected — this is what prevents repeat outreach.
    """
    key = build_idempotency_key(strategy_id, action_type, target)
    existing = (
        await db.execute(
            select(AgentAction).where(AgentAction.idempotency_key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.state not in ("cancelled", "rejected"):
            raise DuplicateAction(
                f"{action_type} for this target is already {existing.state}"
            )
        # A previously abandoned action may be retried, but the key must stay unique.
        existing.idempotency_key = f"{key}:{int(datetime.now(UTC).timestamp())}"
        await db.flush()

    gated = requires_approval_for(action_type, trusted=trusted)
    action = AgentAction(
        run_id=run.id if run is not None else None,
        strategy_id=strategy_id,
        task_id=task_id,
        action_type=action_type,
        state="awaiting_approval" if gated else "queued",
        requires_approval=gated,
        payload=payload or {},
        rationale=rationale,
        idempotency_key=key,
    )
    db.add(action)
    await db.flush()

    if gated:
        db.add(
            Approval(
                action_id=action.id,
                strategy_id=strategy_id,
                status="pending",
                risk=risk,
                request_summary=rationale,
            )
        )

    await record_audit(
        db,
        strategy_id=strategy_id,
        actor_type="agent",
        actor_label=run.agent_key if run is not None else None,
        action="action.proposed",
        entity_type="agent_action",
        entity_id=action.id,
        after={"action_type": action_type, "state": action.state},
    )
    await db.flush()
    return action


async def transition(
    db: AsyncSession,
    action: AgentAction,
    new_state: str,
    *,
    actor_type: str = "agent",
    actor_id: int | None = None,
    error: str | None = None,
    result: dict | None = None,
) -> AgentAction:
    """Move an action to ``new_state``, refusing illegal jumps."""
    allowed = ALLOWED_TRANSITIONS.get(action.state, frozenset())
    if new_state not in allowed:
        raise TransitionError(
            f"cannot move action {action.id} from {action.state!r} to {new_state!r}"
        )

    previous = action.state
    action.state = new_state
    now = datetime.now(UTC)

    if new_state == "in_progress":
        action.started_at = action.started_at or now
        action.attempts += 1
    elif new_state == "completed":
        action.completed_at = now
        if result is not None:
            action.result = result
    elif new_state == "failed":
        action.last_error = error
    elif new_state == "retrying":
        action.last_error = error

    await record_audit(
        db,
        strategy_id=action.strategy_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="action.transition",
        entity_type="agent_action",
        entity_id=action.id,
        before={"state": previous},
        after={"state": new_state},
    )
    await db.flush()
    return action


async def decide_approval(
    db: AsyncSession,
    approval: Approval,
    *,
    approved: bool,
    user: User,
    reason: str | None = None,
) -> Approval:
    """Record a human decision and move the underlying action accordingly."""
    if approval.status != "pending":
        raise TransitionError(f"approval {approval.id} is already {approval.status}")

    action = await db.get(AgentAction, approval.action_id)
    if action is None:
        raise TransitionError(f"approval {approval.id} has no action")

    approval.status = "approved" if approved else "rejected"
    approval.decided_at = datetime.now(UTC)
    approval.decided_by_id = user.id
    approval.decision_reason = reason

    await transition(
        db,
        action,
        "approved" if approved else "rejected",
        actor_type="human",
        actor_id=user.id,
    )
    if approved:
        await transition(db, action, "queued", actor_type="human", actor_id=user.id)

    await db.flush()
    return approval


async def can_retry(action: AgentAction) -> bool:
    return action.state == "failed" and action.attempts < action.max_attempts


async def evidence_count(db: AsyncSession, task_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.task_id == task_id)
    )
    return int(result.scalar_one())


async def blocking_dependencies(
    db: AsyncSession, task: StrategyTask
) -> list[StrategyTask]:
    """Return the task's unfinished prerequisites."""
    ids = [int(i) for i in (task.depends_on_ids or [])]
    if not ids:
        return []
    rows = (
        await db.execute(
            select(StrategyTask).where(
                StrategyTask.id.in_(ids), StrategyTask.status != "done"
            )
        )
    ).scalars().all()
    return list(rows)


async def complete_task(
    db: AsyncSession,
    task: StrategyTask,
    *,
    user: User,
    override_reason: str | None = None,
) -> StrategyTask:
    """Complete a task, enforcing its evidence requirement.

    Raises :class:`EvidenceRequired` when the task is gated, has no evidence, and no
    override reason was supplied. The override is deliberately not silent: it is stored
    on the task and written to the audit log.
    """
    if task.requires_evidence:
        count = await evidence_count(db, task.id)
        if count == 0:
            if not override_reason:
                raise EvidenceRequired(
                    f"'{task.title}' requires evidence before it can be completed. "
                    f"Acceptance criteria: {task.acceptance_criteria or 'not set'}"
                )
            task.override_reason = override_reason

    previous = task.status
    task.status = "done"
    task.completed_at = datetime.now(UTC)
    task.verified_by_id = user.id
    task.verified_at = task.completed_at

    await record_audit(
        db,
        strategy_id=task.strategy_id,
        actor_type="human",
        actor_id=user.id,
        action="task.completed",
        entity_type="strategy_task",
        entity_id=task.id,
        before={"status": previous},
        after={"status": "done", "override": bool(task.override_reason)},
    )
    await db.flush()
    return task


async def record_kpi(
    db: AsyncSession,
    *,
    strategy_id: int,
    pillar: str,
    kpi_key: str,
    value: float,
    source_task_id: int | None = None,
    note: str | None = None,
) -> KpiSnapshot:
    """Append a KPI reading, computing the delta against the previous one."""
    previous = (
        await db.execute(
            select(KpiSnapshot)
            .where(
                KpiSnapshot.strategy_id == strategy_id,
                KpiSnapshot.kpi_key == kpi_key,
            )
            .order_by(KpiSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    snapshot = KpiSnapshot(
        strategy_id=strategy_id,
        pillar=pillar,
        kpi_key=kpi_key,
        value=value,
        delta=None if previous is None else value - previous.value,
        source_task_id=source_task_id,
        note=note,
    )
    db.add(snapshot)
    await db.flush()
    return snapshot
