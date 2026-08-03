"""Execution API — task trees, agent activity, the approval queue, and evidence.

Mounted under ``/api/v1/execution``. The existing ``/strategy`` router keeps its
task endpoints so nothing that already works changes behaviour; this router adds the
execution layer on top.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import executor, orchestrator
from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.execution import (
    PRE_AUTHORISABLE_ACTION_TYPES,
    AgentAction,
    AgentRun,
    Approval,
    AuditLog,
    Evidence,
    KpiSnapshot,
    PreAuthorizationGrant,
)
from app.models.strategy import Strategy, StrategyTask
from app.models.user import User
from app.schemas.execution import (
    AgentActionOut,
    AgentRunOut,
    ApprovalDecision,
    ApprovalOut,
    ApprovalQueueItem,
    AuditLogOut,
    EvidenceCreate,
    EvidenceOut,
    ExecutionOutcomeOut,
    ExecutionRunOut,
    GrantCreate,
    GrantOut,
    GrantPauseRequest,
    KpiSnapshotOut,
    PlanRunOut,
    PolicyPreviewOut,
    PolicyPreviewRequest,
    TaskCompleteRequest,
    TaskNode,
)
from app.services import approval_policy, execution_service
from app.services.approval_policy import body_fingerprint

router = APIRouter()


async def _get_strategy(db: AsyncSession, strategy_id: int) -> Strategy:
    obj = await db.get(Strategy, strategy_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return obj


async def _get_task(db: AsyncSession, task_id: int) -> StrategyTask:
    obj = await db.get(StrategyTask, task_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return obj


@router.get("/strategies/{strategy_id}/tasks/tree", response_model=list[TaskNode])
async def get_task_tree(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[TaskNode]:
    """Return the strategy's tasks as a nested tree, roots first.

    Tasks created before the execution layer have no ``parent_id``, so they simply
    appear as roots — old plans stay visible and usable.
    """
    await _get_strategy(db, strategy_id)

    rows = (
        await db.execute(
            select(StrategyTask)
            .where(StrategyTask.strategy_id == strategy_id)
            .order_by(StrategyTask.position, StrategyTask.id)
        )
    ).scalars().all()

    counts = dict(
        (
            await db.execute(
                select(Evidence.task_id, func.count())
                .where(Evidence.task_id.in_([r.id for r in rows] or [0]))
                .group_by(Evidence.task_id)
            )
        ).all()
    )
    done_ids = {r.id for r in rows if r.status == "done"}

    nodes: dict[int, TaskNode] = {}
    for row in rows:
        depends = [int(i) for i in (row.depends_on_ids or [])]
        node = TaskNode.model_validate(row)
        node.depends_on_ids = depends
        node.evidence_count = int(counts.get(row.id, 0))
        node.blocked_by = [i for i in depends if i not in done_ids]
        node.children = []
        nodes[row.id] = node

    roots: list[TaskNode] = []
    for row in rows:
        node = nodes[row.id]
        parent = nodes.get(row.parent_id) if row.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)
    return roots


@router.post("/tasks/{task_id}/complete", response_model=TaskNode)
async def complete_task(
    task_id: int,
    payload: TaskCompleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskNode:
    """Complete a task, refusing if it is gated on evidence and has none.

    A 409 here is the point of the whole feature: it is the system declining to
    record an outcome that has not actually happened.
    """
    task = await _get_task(db, task_id)

    blocking = await execution_service.blocking_dependencies(db, task)
    if blocking:
        titles = ", ".join(t.title for t in blocking[:3])
        raise HTTPException(
            status_code=409,
            detail=f"Blocked by {len(blocking)} unfinished prerequisite(s): {titles}",
        )

    try:
        await execution_service.complete_task(
            db, task, user=user, override_reason=payload.override_reason
        )
    except execution_service.EvidenceRequired as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(task)

    node = TaskNode.model_validate(task)
    node.depends_on_ids = [int(i) for i in (task.depends_on_ids or [])]
    node.evidence_count = await execution_service.evidence_count(db, task.id)
    return node


@router.get("/tasks/{task_id}/evidence", response_model=list[EvidenceOut])
async def list_evidence(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[EvidenceOut]:
    await _get_task(db, task_id)
    rows = (
        await db.execute(
            select(Evidence)
            .where(Evidence.task_id == task_id)
            .order_by(Evidence.created_at.desc())
        )
    ).scalars().all()
    return [EvidenceOut.model_validate(r) for r in rows]


@router.post("/tasks/{task_id}/evidence", response_model=EvidenceOut, status_code=201)
async def add_evidence(
    task_id: int,
    payload: EvidenceCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EvidenceOut:
    task = await _get_task(db, task_id)
    record = Evidence(
        task_id=task.id,
        kind=payload.kind,
        description=payload.description,
        email_message_id=payload.email_message_id,
        document_id=payload.document_id,
        opportunity_id=payload.opportunity_id,
        payload=payload.payload,
        created_by_type="human",
        created_by_id=user.id,
    )
    db.add(record)
    await execution_service.record_audit(
        db,
        strategy_id=task.strategy_id,
        actor_type="human",
        actor_id=user.id,
        action="evidence.added",
        entity_type="strategy_task",
        entity_id=task.id,
        after={"kind": payload.kind},
    )
    await db.commit()
    await db.refresh(record)
    return EvidenceOut.model_validate(record)


@router.get(
    "/strategies/{strategy_id}/approvals", response_model=list[ApprovalQueueItem]
)
async def approval_queue(
    strategy_id: int,
    status: str = Query("pending"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ApprovalQueueItem]:
    """Everything waiting on a human decision, newest last so the queue reads as a list."""
    await _get_strategy(db, strategy_id)
    rows = (
        await db.execute(
            select(Approval)
            .where(Approval.strategy_id == strategy_id, Approval.status == status)
            .order_by(Approval.created_at)
        )
    ).scalars().all()

    items: list[ApprovalQueueItem] = []
    for approval in rows:
        action = await db.get(AgentAction, approval.action_id)
        if action is None:
            continue
        task_title: str | None = None
        if action.task_id is not None:
            task = await db.get(StrategyTask, action.task_id)
            task_title = task.title if task is not None else None
        items.append(
            ApprovalQueueItem(
                approval=ApprovalOut.model_validate(approval),
                action=AgentActionOut.model_validate(action),
                task_title=task_title,
            )
        )
    return items


@router.post("/approvals/{approval_id}/decide", response_model=ApprovalOut)
async def decide_approval(
    approval_id: int,
    payload: ApprovalDecision,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApprovalOut:
    approval = await db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    try:
        await execution_service.decide_approval(
            db, approval, approved=payload.approved, user=user, reason=payload.reason
        )
    except execution_service.TransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Approving is the decision to act, so the action runs now rather than waiting for
    # someone to remember to trigger execution.
    if payload.approved:
        action = await db.get(AgentAction, approval.action_id)
        if action is not None and action.state == "queued":
            await executor.run_action(db, action, user_id=user.id)
    await db.commit()
    await db.refresh(approval)
    return ApprovalOut.model_validate(approval)


@router.post("/strategies/{strategy_id}/plan", response_model=PlanRunOut)
async def run_orchestrator(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PlanRunOut:
    """Decompose the strategy's outstanding gaps into a task tree.

    Planning never sends anything. It produces evidence-gated tasks; acting on them is
    a separate, approval-governed step.
    """
    strategy = await _get_strategy(db, strategy_id)
    run, created = await orchestrator.plan(db, strategy)
    await db.commit()
    await db.refresh(run)
    return PlanRunOut(
        run=AgentRunOut.model_validate(run),
        created_task_ids=[t.id for t in created],
    )


@router.post("/strategies/{strategy_id}/execute", response_model=ExecutionRunOut)
async def run_executor(
    strategy_id: int,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExecutionRunOut:
    """Act on the planned work: research, draft, and send what policy clears.

    Every outbound message is judged on its content before it goes anywhere, so a run
    that returns only ``awaiting_approval`` outcomes is working as intended, not failing.
    """
    strategy = await _get_strategy(db, strategy_id)
    report = await executor.execute(
        db, strategy, user_id=user.id, limit=max(1, min(limit, 50))
    )
    await db.commit()
    run = await db.get(AgentRun, report.run_id)
    return ExecutionRunOut(
        run=AgentRunOut.model_validate(run),
        outcomes=[
            ExecutionOutcomeOut(
                action_id=o.action_id,
                task_id=o.task_id,
                capability=o.capability,
                state=o.state,
                detail=o.detail,
            )
            for o in report.outcomes
        ],
    )


@router.post("/strategies/{strategy_id}/resume", response_model=list[AgentActionOut])
async def resume_after_replies(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AgentActionOut]:
    """Wake any actions parked waiting for a reply that has since arrived."""
    strategy = await _get_strategy(db, strategy_id)
    resumed = await orchestrator.resume_from_replies(db, strategy)
    await db.commit()
    return [AgentActionOut.model_validate(a) for a in resumed]


@router.get("/strategies/{strategy_id}/grants", response_model=list[GrantOut])
async def list_grants(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[GrantOut]:
    await _get_strategy(db, strategy_id)
    rows = (
        await db.execute(
            select(PreAuthorizationGrant)
            .where(PreAuthorizationGrant.strategy_id == strategy_id)
            .order_by(PreAuthorizationGrant.created_at.desc())
        )
    ).scalars().all()
    return [GrantOut.model_validate(r) for r in rows]


@router.post(
    "/strategies/{strategy_id}/grants", response_model=GrantOut, status_code=201
)
async def create_grant(
    strategy_id: int,
    payload: GrantCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GrantOut:
    """Create a standing authorisation for constrained follow-ups on one thread.

    Only action types in ``PRE_AUTHORISABLE_ACTION_TYPES`` may be granted; anything
    irreversible is refused here rather than being silently ignored at evaluation time.
    """
    await _get_strategy(db, strategy_id)
    if payload.action_type not in PRE_AUTHORISABLE_ACTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"'{payload.action_type}' cannot be pre-authorised; it requires an "
                "explicit decision every time."
            ),
        )

    grant = PreAuthorizationGrant(
        strategy_id=strategy_id,
        created_by_id=user.id,
        action_type=payload.action_type,
        thread_key=payload.thread_key,
        recipient=payload.recipient,
        template_key=payload.template_key,
        approved_body_hash=(
            body_fingerprint(payload.approved_body)
            if payload.approved_body is not None
            else None
        ),
        max_messages=payload.max_messages,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    db.add(grant)
    await db.flush()
    await execution_service.record_audit(
        db,
        strategy_id=strategy_id,
        actor_type="human",
        actor_id=user.id,
        action="grant.created",
        entity_type="pre_authorization_grant",
        entity_id=grant.id,
        after={
            "action_type": grant.action_type,
            "recipient": grant.recipient,
            "max_messages": grant.max_messages,
        },
    )
    await db.commit()
    await db.refresh(grant)
    return GrantOut.model_validate(grant)


@router.post("/grants/{grant_id}/pause", response_model=GrantOut)
async def pause_grant(
    grant_id: int,
    payload: GrantPauseRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GrantOut:
    """The immediate stop control — takes effect on the next evaluation, no wind-down."""
    grant = await db.get(PreAuthorizationGrant, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    grant.paused = payload.paused
    await execution_service.record_audit(
        db,
        strategy_id=grant.strategy_id,
        actor_type="human",
        actor_id=user.id,
        action="grant.paused" if payload.paused else "grant.resumed",
        entity_type="pre_authorization_grant",
        entity_id=grant.id,
        after={"paused": payload.paused},
    )
    await db.commit()
    await db.refresh(grant)
    return GrantOut.model_validate(grant)


@router.delete("/grants/{grant_id}", response_model=GrantOut)
async def revoke_grant(
    grant_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GrantOut:
    grant = await db.get(PreAuthorizationGrant, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    grant.revoked_at = datetime.now(UTC)
    await execution_service.record_audit(
        db,
        strategy_id=grant.strategy_id,
        actor_type="human",
        actor_id=user.id,
        action="grant.revoked",
        entity_type="pre_authorization_grant",
        entity_id=grant.id,
        after={"revoked": True},
    )
    await db.commit()
    await db.refresh(grant)
    return GrantOut.model_validate(grant)


@router.post(
    "/strategies/{strategy_id}/policy/preview", response_model=PolicyPreviewOut
)
async def preview_policy(
    strategy_id: int,
    payload: PolicyPreviewRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PolicyPreviewOut:
    """Show what the policy would decide for a draft, and why, without sending it."""
    await _get_strategy(db, strategy_id)
    decision = await approval_policy.evaluate(
        db,
        strategy_id=strategy_id,
        action_type=payload.action_type,
        email=approval_policy.EmailContext(
            recipient=payload.recipient,
            body=payload.body,
            subject=payload.subject,
            thread_key=payload.thread_key,
            template_key=payload.template_key,
            has_attachments=payload.has_attachments,
            materially_changed=payload.materially_changed,
        ),
    )
    return PolicyPreviewOut(
        requires_approval=decision.requires_approval,
        reason=decision.reason,
        risk=decision.risk,
        grant_id=decision.grant_id,
        triggers=list(decision.triggers),
    )


@router.get("/strategies/{strategy_id}/runs", response_model=list[AgentRunOut])
async def list_runs(
    strategy_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AgentRunOut]:
    await _get_strategy(db, strategy_id)
    rows = (
        await db.execute(
            select(AgentRun)
            .where(AgentRun.strategy_id == strategy_id)
            .order_by(AgentRun.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [AgentRunOut.model_validate(r) for r in rows]


@router.get("/strategies/{strategy_id}/actions", response_model=list[AgentActionOut])
async def list_actions(
    strategy_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AgentActionOut]:
    await _get_strategy(db, strategy_id)
    rows = (
        await db.execute(
            select(AgentAction)
            .where(AgentAction.strategy_id == strategy_id)
            .order_by(AgentAction.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [AgentActionOut.model_validate(r) for r in rows]


@router.get("/strategies/{strategy_id}/kpis", response_model=list[KpiSnapshotOut])
async def list_kpis(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[KpiSnapshotOut]:
    await _get_strategy(db, strategy_id)
    rows = (
        await db.execute(
            select(KpiSnapshot)
            .where(KpiSnapshot.strategy_id == strategy_id)
            .order_by(KpiSnapshot.created_at.desc())
        )
    ).scalars().all()
    return [KpiSnapshotOut.model_validate(r) for r in rows]


@router.get("/strategies/{strategy_id}/audit", response_model=list[AuditLogOut])
async def list_audit(
    strategy_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AuditLogOut]:
    await _get_strategy(db, strategy_id)
    rows = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.strategy_id == strategy_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]
