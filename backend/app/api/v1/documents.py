from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.deal import Deal
from app.models.document import Document
from app.models.supplier import Supplier
from app.models.user import User
from app.schemas.document import DocumentGenerateRequest, DocumentOut, DocumentUpdate
from app.services.document_generation import DocumentGenerationService

router = APIRouter()


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    deal_id: int | None = None,
    supplier_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[DocumentOut]:
    stmt = select(Document).order_by(Document.created_at.desc())
    if deal_id is not None:
        stmt = stmt.where(Document.deal_id == deal_id)
    if supplier_id is not None:
        stmt = stmt.where(Document.supplier_id == supplier_id)
    result = await db.execute(stmt)
    return [DocumentOut.model_validate(d) for d in result.scalars().all()]


@router.post("/generate", response_model=DocumentOut, status_code=201)
async def generate_document(
    payload: DocumentGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentOut:
    inputs: dict = dict(payload.inputs or {})
    if payload.deal_id:
        deal = await db.get(Deal, payload.deal_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        inputs.setdefault(
            "deal",
            {
                "commodity": deal.commodity,
                "volume_mt": deal.volume_mt,
                "buy_price": deal.buy_price,
                "sell_price": deal.sell_price,
                "freight_estimate": deal.freight_estimate,
                "incoterms": deal.incoterms,
                "currency": deal.currency,
            },
        )
    if payload.supplier_id:
        supplier = await db.get(Supplier, payload.supplier_id)
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")
        inputs.setdefault(
            "supplier",
            {
                "name": supplier.name,
                "country": supplier.country,
                "email": supplier.email,
                "website": supplier.website,
            },
        )

    service = DocumentGenerationService()
    title, content = await service.generate(payload.type, inputs)

    doc = Document(
        type=payload.type,
        title=title,
        content=content,
        inputs=inputs,
        deal_id=payload.deal_id,
        supplier_id=payload.supplier_id,
        created_by=user.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return DocumentOut.model_validate(doc)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DocumentOut:
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentOut.model_validate(doc)


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> DocumentOut:
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(doc, k, v)
    await db.commit()
    await db.refresh(doc)
    return DocumentOut.model_validate(doc)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> None:
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    await db.delete(doc)
    await db.commit()


@router.get("/{document_id}/export.docx")
async def export_docx(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    service = DocumentGenerationService()
    data = service.to_docx(doc.title, doc.content)
    filename = f"{doc.type}-{doc.id}.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{document_id}/export.md")
async def export_md(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> Response:
    doc = await db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    filename = f"{doc.type}-{doc.id}.md"
    return Response(
        content=doc.content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
