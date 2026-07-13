"""Strategy engine API — north-star strategies, pillar objectives, and the
week-by-week / day-to-day cadence board.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.strategy import Strategy, StrategyTask
from app.models.user import User
from app.schemas.strategy import (
    GeneratePlanRequest,
    StrategyBoard,
    StrategyCreate,
    StrategyOut,
    StrategyTaskCreate,
    StrategyTaskOut,
    StrategyTaskUpdate,
    StrategyUpdate,
)
from app.services import strategy_service

router = APIRouter()


async def _get_strategy(db: AsyncSession, strategy_id: int) -> Strategy:
    obj = await db.get(Strategy, strategy_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return obj


@router.get("", response_model=list[StrategyOut])
async def list_strategies(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[StrategyOut]:
    rows = (
        await db.execute(select(Strategy).order_by(Strategy.created_at.desc()))
    ).scalars().all()
    return [StrategyOut.model_validate(r) for r in rows]


@router.post("", response_model=StrategyOut, status_code=201)
async def create_strategy(
    payload: StrategyCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StrategyOut:
    data = payload.model_dump(exclude={"auto_plan"})
    obj = Strategy(**data, owner_id=user.id, status="active", pillars={})
    if payload.auto_plan:
        obj.pillars = await strategy_service.draft_pillars(obj)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return StrategyOut.model_validate(obj)


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StrategyOut:
    obj = await _get_strategy(db, strategy_id)
    return StrategyOut.model_validate(obj)


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: int,
    payload: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StrategyOut:
    obj = await _get_strategy(db, strategy_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return StrategyOut.model_validate(obj)


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    obj = await _get_strategy(db, strategy_id)
    await db.delete(obj)
    await db.commit()


@router.post("/{strategy_id}/replan-pillars", response_model=StrategyOut)
async def replan_pillars(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StrategyOut:
    obj = await _get_strategy(db, strategy_id)
    obj.pillars = await strategy_service.draft_pillars(obj)
    await db.commit()
    await db.refresh(obj)
    return StrategyOut.model_validate(obj)


@router.post("/{strategy_id}/generate-plan", response_model=list[StrategyTaskOut])
async def generate_plan(
    strategy_id: int,
    payload: GeneratePlanRequest | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[StrategyTaskOut]:
    obj = await _get_strategy(db, strategy_id)
    week_start = payload.week_start if payload else None
    tasks = await strategy_service.generate_plan(db, obj, week_start)
    return [StrategyTaskOut.model_validate(t) for t in tasks]


@router.get("/{strategy_id}/board", response_model=StrategyBoard)
async def get_board(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StrategyBoard:
    obj = await _get_strategy(db, strategy_id)
    week_start = strategy_service._monday(datetime.now(UTC).date())
    pillars = await strategy_service.build_pillar_progress(db, obj, week_start)
    week_tasks = (
        await db.execute(
            select(StrategyTask)
            .where(
                StrategyTask.strategy_id == strategy_id,
                StrategyTask.week_start == week_start,
            )
            .order_by(StrategyTask.priority.asc(), StrategyTask.id.asc())
        )
    ).scalars().all()
    today_tasks = strategy_service.select_today_tasks(list(week_tasks))

    behind = [p for p in pillars if p.status in ("behind", "idle")]
    if behind:
        headline = (
            "Focus this week: "
            + ", ".join(p.label for p in behind[:3])
            + " need attention."
        )
    else:
        headline = "All four pillars on track — keep executing the cadence."

    return StrategyBoard(
        strategy=StrategyOut.model_validate(obj),
        week_start=week_start,
        pillars=pillars,
        week_tasks=[StrategyTaskOut.model_validate(t) for t in week_tasks],
        today_tasks=[StrategyTaskOut.model_validate(t) for t in today_tasks],
        headline=headline,
    )


# --- Tasks ----------------------------------------------------------------


@router.post("/{strategy_id}/tasks", response_model=StrategyTaskOut, status_code=201)
async def create_task(
    strategy_id: int,
    payload: StrategyTaskCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StrategyTaskOut:
    await _get_strategy(db, strategy_id)
    data = payload.model_dump(exclude_unset=True)
    if not data.get("week_start"):
        data["week_start"] = strategy_service._monday(datetime.now(UTC).date())
    task = StrategyTask(strategy_id=strategy_id, source="manual", status="todo", **data)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return StrategyTaskOut.model_validate(task)


@router.patch(
    "/{strategy_id}/tasks/{task_id}", response_model=StrategyTaskOut
)
async def update_task(
    strategy_id: int,
    task_id: int,
    payload: StrategyTaskUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> StrategyTaskOut:
    task = await db.get(StrategyTask, task_id)
    if task is None or task.strategy_id != strategy_id:
        raise HTTPException(status_code=404, detail="Task not found")
    changes = payload.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(task, k, v)
    if changes.get("status") == "done" and task.completed_at is None:
        task.completed_at = datetime.now(UTC)
    if changes.get("status") in ("todo", "doing"):
        task.completed_at = None
    await db.commit()
    await db.refresh(task)
    return StrategyTaskOut.model_validate(task)


@router.delete("/{strategy_id}/tasks/{task_id}", status_code=204)
async def delete_task(
    strategy_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    task = await db.get(StrategyTask, task_id)
    if task is None or task.strategy_id != strategy_id:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.delete(task)
    await db.commit()
