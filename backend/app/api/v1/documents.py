from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.negotiation_strategy import (
    NegotiationContext,
    NegotiationStage,
    build_disclosure_guidance,
)
from app.core.db import get_db
from app.core.deps import get_current_user
from app.integrations.yahoo_finance import get_price
from app.models.deal import Deal
from app.models.document import Document
from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
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

    # Current user — signer / sender. Personalises outreach emails and doc signatures.
    inputs.setdefault(
        "sender",
        {
            "full_name": user.full_name or user.email.split("@")[0],
            "company_name": user.company_name or "(your company — set it in Profile)",
            "title": user.title or "Trader",
            "email": user.email,
            "phone": user.phone,
        },
    )

    supplier_id = payload.supplier_id

    # Resolve opportunity + lead context so we can auto-inject the negotiation
    # block for stage-aware emails. This is best-effort — if any of the ids are
    # missing or invalid we just skip the block and the LLM falls back to the
    # default stage-1 rules encoded in the prompt.
    opportunity: Opportunity | None = None
    supplier_lead: SupplierLead | None = None
    buyer_lead: BuyerLead | None = None
    if payload.opportunity_id:
        opportunity = await db.get(Opportunity, payload.opportunity_id)
    if payload.supplier_lead_id:
        supplier_lead = await db.get(SupplierLead, payload.supplier_lead_id)
    if payload.buyer_lead_id:
        buyer_lead = await db.get(BuyerLead, payload.buyer_lead_id)

    if payload.deal_id:
        deal = await db.get(Deal, payload.deal_id)
        if not deal:
            raise HTTPException(status_code=404, detail="Deal not found")
        inputs.setdefault(
            "deal",
            {
                "title": deal.title,
                "commodity": deal.commodity,
                "volume_mt": deal.volume_mt,
                "buy_price": deal.buy_price,
                "sell_price": deal.sell_price,
                "freight_estimate": deal.freight_estimate,
                "incoterms": deal.incoterms,
                "currency": deal.currency,
                "structure": deal.structure,
            },
        )
        # If the deal has a linked supplier, auto-use it when supplier_id not passed.
        if supplier_id is None and deal.supplier_id is not None:
            supplier_id = deal.supplier_id

    if supplier_id:
        supplier = await db.get(Supplier, supplier_id)
        if not supplier:
            raise HTTPException(status_code=404, detail="Supplier not found")
        inputs.setdefault(
            "supplier",
            {
                "name": supplier.name,
                "country": supplier.country,
                "commodity": supplier.commodity,
                "email": supplier.email,
                "website": supplier.website,
                "type": supplier.type,
            },
        )
    elif supplier_lead is not None and "supplier" not in inputs:
        # Fallback: build a `supplier` block from the SupplierLead's inline
        # fields so stage-aware emails work even for leads that were never
        # promoted to a full Supplier counterparty record.
        opp_commodity = opportunity.commodity if opportunity else None
        inputs["supplier"] = {
            "name": supplier_lead.supplier_name,
            "country": supplier_lead.country,
            "commodity": opp_commodity,
            "email": supplier_lead.email,
            "website": None,
            "type": None,
        }

    # For outreach-family emails, derive the `negotiation` block from the
    # referenced lead so the LLM has the explicit disclosure matrix to obey.
    if payload.type in {"outreach_email", "counter_offer_email", "follow_up_email"}:
        active_lead = supplier_lead or buyer_lead
        if active_lead is not None and "negotiation" not in inputs:
            # Auto-inject the supplier's quoted price (and incoterms / payment)
            # as the `supplier_quote` block so stage-3 follow-ups have a real
            # anchor to counter against. Caller-provided `supplier_quote` in
            # inputs wins. `intel.quoted_price_usd_mt` (manually logged from a
            # reply) takes precedence over the lead's static `price_mt`.
            if supplier_lead is not None and "supplier_quote" not in inputs:
                intel = dict(supplier_lead.intel or {})
                quoted_price = intel.get("quoted_price_usd_mt") or supplier_lead.price_mt
                if quoted_price:
                    inputs["supplier_quote"] = {
                        "price_mt": quoted_price,
                        "incoterms": supplier_lead.quoted_incoterms,
                        "payment_terms": supplier_lead.payment_terms,
                        "min_order_mt": supplier_lead.min_order_mt,
                        "lead_time_days": supplier_lead.lead_time_days,
                    }

            stage = NegotiationStage(max(1, min(5, active_lead.negotiation_stage or 1)))
            ctx = NegotiationContext(
                stage=stage,
                side="supplier" if supplier_lead is not None else "buyer",
                intel=dict(active_lead.intel or {}),
                disclosed=dict(active_lead.disclosed or {}),
                market_reference=inputs.get("market_reference"),
                supplier_quote=inputs.get("supplier_quote"),
                last_supplier_response=inputs.get("last_supplier_response"),
            )
            inputs["negotiation"] = build_disclosure_guidance(ctx)

    # For counter-offer / stage-3 follow-up emails, auto-inject the live futures reference
    # so the LLM has a transparent anchor to justify the counter against. Best-effort — if
    # the feed is down we omit the block rather than fail the document generation.
    needs_market_ref = payload.type == "counter_offer_email" or (
        payload.type == "follow_up_email"
        and (inputs.get("negotiation") or {}).get("stage") == 3
    )
    if needs_market_ref and "market_reference" not in inputs:
        commodity_hint = (
            (inputs.get("supplier") or {}).get("commodity")
            or (inputs.get("deal") or {}).get("commodity")
            or (opportunity.commodity if opportunity else None)
        )
        if commodity_hint:
            try:
                quote = await get_price(commodity_hint)
            except Exception:
                quote = None
            if quote is not None:
                inputs["market_reference"] = {
                    "commodity": quote.commodity,
                    "ticker": quote.ticker,
                    "exchange": quote.exchange,
                    "price_mt": quote.price_mt,
                    "raw_price": quote.raw_price,
                    "quoted_unit": quote.quoted_unit,
                    "source": quote.source,
                    "timestamp": quote.timestamp,
                }
                # Re-surface the market_reference into the negotiation block so
                # the LLM sees it via the disclosure-guidance section too.
                if isinstance(inputs.get("negotiation"), dict):
                    inputs["negotiation"]["market_reference"] = inputs[
                        "market_reference"
                    ]

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
