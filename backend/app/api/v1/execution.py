"""Execution API — task trees, agent activity, the approval queue, and evidence.

Mounted under ``/api/v1/execution``. The existing ``/strategy`` router keeps its
task endpoints so nothing that already works changes behaviour; this router adds the
execution layer on top.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.execution import (
    AgentAction,
    AgentRun,
    Approval,
    AuditLog,
    Evidence,
    KpiSnapshot,
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
    KpiSnapshotOut,
    TaskCompleteRequest,
    TaskNode,
)
from app.services import execution_service

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
    await db.commit()
    await db.refresh(approval)
    return ApprovalOut.model_validate(approval)


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
