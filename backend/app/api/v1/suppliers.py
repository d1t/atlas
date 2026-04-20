from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.supplier import Supplier
from app.models.user import User
from app.schemas.supplier import (
    ClassificationResult,
    DiscoveryRequest,
    SupplierCreate,
    SupplierOut,
    SupplierUpdate,
)
from app.services.counterparty import CounterpartyService
from app.services.supplier_discovery import SupplierDiscoveryService

router = APIRouter()


@router.get("", response_model=list[SupplierOut])
async def list_suppliers(
    q: str | None = Query(None),
    country: str | None = Query(None),
    commodity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SupplierOut]:
    stmt = select(Supplier).order_by(Supplier.created_at.desc()).limit(limit)
    if q:
        stmt = stmt.where(Supplier.name.ilike(f"%{q}%"))
    if country:
        stmt = stmt.where(Supplier.country.ilike(f"%{country}%"))
    if commodity:
        stmt = stmt.where(Supplier.commodity.ilike(f"%{commodity}%"))
    result = await db.execute(stmt)
    return [SupplierOut.model_validate(s) for s in result.scalars().all()]


@router.post("", response_model=SupplierOut, status_code=201)
async def create_supplier(
    payload: SupplierCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SupplierOut:
    supplier = Supplier(**payload.model_dump())
    cp = CounterpartyService()
    scored = cp.score(supplier)
    supplier.credibility_score = scored["credibility_score"]
    supplier.risk_score = scored["risk_score"]
    supplier.red_flags = scored["red_flags"]
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return SupplierOut.model_validate(supplier)


@router.get("/{supplier_id}", response_model=SupplierOut)
async def get_supplier(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SupplierOut:
    supplier = await db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return SupplierOut.model_validate(supplier)


@router.patch("/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> SupplierOut:
    supplier = await db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(supplier, k, v)
    cp = CounterpartyService()
    scored = cp.score(supplier)
    supplier.credibility_score = scored["credibility_score"]
    supplier.risk_score = scored["risk_score"]
    supplier.red_flags = scored["red_flags"]
    await db.commit()
    await db.refresh(supplier)
    return SupplierOut.model_validate(supplier)


@router.delete("/{supplier_id}", status_code=204)
async def delete_supplier(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    supplier = await db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    await db.delete(supplier)
    await db.commit()


@router.post("/discover", response_model=list[SupplierOut])
async def discover_suppliers(
    payload: DiscoveryRequest,
    persist: bool = Query(True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SupplierOut]:
    service = SupplierDiscoveryService(db)
    candidates = await service.discover(
        commodity=payload.commodity, country=payload.country, limit=payload.limit
    )
    if not persist:
        return [
            SupplierOut.model_validate(
                Supplier(
                    id=0,
                    name=c.name,
                    type=c.type,
                    country=c.country,
                    commodity=c.commodity,
                    website=c.website,
                    email=c.email,
                    phone=c.phone,
                    contact_name=c.contact_name,
                    description=c.description,
                    source=c.source,
                    red_flags=[],
                    extra_data={},
                )
            )
            for c in candidates
        ]
    created = await service.persist(candidates)
    cp = CounterpartyService()
    for s in created:
        scored = cp.score(s)
        s.credibility_score = scored["credibility_score"]
        s.risk_score = scored["risk_score"]
        s.red_flags = scored["red_flags"]
    await db.commit()
    for s in created:
        await db.refresh(s)
    return [SupplierOut.model_validate(s) for s in created]


@router.post("/{supplier_id}/classify", response_model=ClassificationResult)
async def classify_supplier(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> ClassificationResult:
    supplier = await db.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")
    cp = CounterpartyService()
    result = await cp.classify(supplier)
    supplier.type = result["type"]
    supplier.classification_confidence = result["confidence"]
    scored = cp.score(supplier)
    supplier.credibility_score = scored["credibility_score"]
    supplier.risk_score = scored["risk_score"]
    supplier.red_flags = scored["red_flags"]
    await db.commit()
    return ClassificationResult(**result)
