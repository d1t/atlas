from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.activity import Activity, Task
from app.models.deal import Deal
from app.models.user import User
from app.schemas.deal import (
    ActivityCreate,
    DealCreate,
    DealOut,
    DealUpdate,
    PricingInput,
    PricingResult,
    StageChange,
    TaskCreate,
    TaskOut,
)
from app.services.deal_structuring import PricingInputs, compute_pricing
from app.services.pipeline import (
    InvalidTransition,
    default_probability,
    transition,
)

router = APIRouter()


def _refresh_metrics(deal: Deal) -> None:
    out = compute_pricing(
        PricingInputs(
            buy_price=deal.buy_price,
            sell_price=deal.sell_price,
            freight_estimate=deal.freight_estimate,
            volume_mt=deal.volume_mt,
            incoterms=deal.incoterms,
        )
    )
    deal.margin_per_mt = out.margin_per_mt
    deal.total_value = out.total_value
    deal.total_margin = out.total_margin
    deal.structure = out.recommended_structure
    deal.metrics = {
        "rationale": out.rationale,
        "scenarios": out.scenarios,
    }


@router.get("", response_model=list[DealOut])
async def list_deals(
    stage: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[DealOut]:
    stmt = select(Deal).order_by(Deal.created_at.desc()).limit(limit)
    if stage:
        stmt = stmt.where(Deal.stage == stage)
    result = await db.execute(stmt)
    return [DealOut.model_validate(d) for d in result.scalars().all()]


@router.post("", response_model=DealOut, status_code=201)
async def create_deal(
    payload: DealCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DealOut:
    deal = Deal(**payload.model_dump(), owner_id=user.id)
    deal.probability = default_probability(deal.stage)
    _refresh_metrics(deal)
    db.add(deal)
    await db.flush()
    db.add(
        Activity(
            deal_id=deal.id,
            user_id=user.id,
            type="system",
            message=f"Deal created in stage '{deal.stage}'",
        )
    )
    await db.commit()
    await db.refresh(deal)
    return DealOut.model_validate(deal)


@router.get("/{deal_id}", response_model=DealOut)
async def get_deal(
    deal_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DealOut:
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return DealOut.model_validate(deal)


@router.patch("/{deal_id}", response_model=DealOut)
async def update_deal(
    deal_id: int,
    payload: DealUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DealOut:
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(deal, k, v)
    _refresh_metrics(deal)
    await db.commit()
    await db.refresh(deal)
    return DealOut.model_validate(deal)


@router.delete("/{deal_id}", status_code=204)
async def delete_deal(
    deal_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    await db.delete(deal)
    await db.commit()


@router.post("/{deal_id}/stage", response_model=DealOut)
async def change_stage(
    deal_id: int,
    payload: StageChange,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DealOut:
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    try:
        new_stage = transition(deal.stage, payload.stage)
    except InvalidTransition as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    prev = deal.stage
    deal.stage = new_stage
    deal.probability = default_probability(new_stage)
    db.add(
        Activity(
            deal_id=deal.id,
            user_id=user.id,
            type="stage_change",
            message=f"Stage changed: {prev} -> {new_stage}",
        )
    )
    await db.commit()
    await db.refresh(deal)
    return DealOut.model_validate(deal)


@router.post("/structure", response_model=PricingResult)
async def structure_deal(
    payload: PricingInput,
    _: User = Depends(get_current_user),
) -> PricingResult:
    out = compute_pricing(
        PricingInputs(
            buy_price=payload.buy_price,
            sell_price=payload.sell_price,
            freight_estimate=payload.freight_estimate,
            volume_mt=payload.volume_mt,
            incoterms=payload.incoterms,
        )
    )
    return PricingResult(
        margin_per_mt=out.margin_per_mt,
        total_value=out.total_value,
        total_margin=out.total_margin,
        recommended_structure=out.recommended_structure,
        rationale=out.rationale,
        scenarios=out.scenarios,
    )


@router.get("/{deal_id}/activity")
async def list_activity(
    deal_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    result = await db.execute(
        select(Activity).where(Activity.deal_id == deal_id).order_by(Activity.created_at.desc())
    )
    return [
        {
            "id": a.id,
            "deal_id": a.deal_id,
            "user_id": a.user_id,
            "type": a.type,
            "message": a.message,
            "created_at": a.created_at.isoformat(),
        }
        for a in result.scalars().all()
    ]


@router.post("/{deal_id}/activity")
async def add_activity(
    deal_id: int,
    payload: ActivityCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    deal = await db.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    activity = Activity(
        deal_id=deal_id, user_id=user.id, type=payload.type, message=payload.message
    )
    db.add(activity)
    await db.commit()
    await db.refresh(activity)
    return {
        "id": activity.id,
        "deal_id": activity.deal_id,
        "user_id": activity.user_id,
        "type": activity.type,
        "message": activity.message,
        "created_at": activity.created_at.isoformat(),
    }


@router.get("/{deal_id}/tasks", response_model=list[TaskOut])
async def list_tasks(
    deal_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[TaskOut]:
    result = await db.execute(
        select(Task).where(Task.deal_id == deal_id).order_by(Task.due_at.asc().nullslast())
    )
    tasks = result.scalars().all()
    return [
        TaskOut(
            id=t.id,
            deal_id=t.deal_id,
            title=t.title,
            due_at=t.due_at.isoformat() if t.due_at else None,
            done=t.done,
        )
        for t in tasks
    ]


@router.post("/{deal_id}/tasks", response_model=TaskOut, status_code=201)
async def add_task(
    deal_id: int,
    payload: TaskCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskOut:
    from datetime import datetime

    due = None
    if payload.due_at:
        try:
            due = datetime.fromisoformat(payload.due_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid due_at") from exc
    task = Task(deal_id=deal_id, user_id=user.id, title=payload.title, due_at=due)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return TaskOut(
        id=task.id,
        deal_id=task.deal_id,
        title=task.title,
        due_at=task.due_at.isoformat() if task.due_at else None,
        done=task.done,
    )


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def toggle_task(
    task_id: int,
    done: bool,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> TaskOut:
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.done = done
    await db.commit()
    await db.refresh(task)
    return TaskOut(
        id=task.id,
        deal_id=task.deal_id,
        title=task.title,
        due_at=task.due_at.isoformat() if task.due_at else None,
        done=task.done,
    )
