from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.deal import DEAL_STAGES, Deal
from app.models.user import User
from app.schemas.deal import DealOut

router = APIRouter()


@router.get("/board")
async def pipeline_board(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(select(Deal).order_by(Deal.updated_at.desc()))
    deals = result.scalars().all()
    columns: dict[str, list[DealOut]] = {stage: [] for stage in DEAL_STAGES}
    for d in deals:
        columns.setdefault(d.stage, []).append(DealOut.model_validate(d))
    return {
        "stages": DEAL_STAGES,
        "columns": {stage: [d.model_dump() for d in items] for stage, items in columns.items()},
    }


@router.get("/stats")
async def pipeline_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    by_stage = await db.execute(
        select(Deal.stage, func.count(Deal.id), func.coalesce(func.sum(Deal.total_value), 0))
        .group_by(Deal.stage)
    )
    stage_rows = {stage: {"count": 0, "value": 0.0} for stage in DEAL_STAGES}
    for stage, count, value in by_stage.all():
        stage_rows[stage] = {"count": int(count), "value": float(value or 0)}

    totals = await db.execute(
        select(
            func.count(Deal.id),
            func.coalesce(func.sum(Deal.total_value), 0),
            func.coalesce(func.sum(Deal.total_margin), 0),
        )
    )
    row = totals.one()
    return {
        "by_stage": stage_rows,
        "total_deals": int(row[0]),
        "total_value": float(row[1] or 0),
        "total_margin": float(row[2] or 0),
    }
