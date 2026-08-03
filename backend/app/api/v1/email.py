"""Gmail email API — send outreach/negotiation emails and sync replies."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models.document import Document
from app.models.email import EmailMessage
from app.models.opportunity import BuyerLead, SupplierLead
from app.models.user import User
from app.schemas.email import (
    EmailMessageOut,
    EmailSendRequest,
    GmailStatus,
    ReplySyncResult,
    SendDocumentRequest,
)
from app.services import email_service, integration_service

router = APIRouter()

_SUBJECT_RE = re.compile(r"^\s*subject\s*:\s*(.+)$", re.IGNORECASE)


def _split_subject_body(content: str, fallback_subject: str) -> tuple[str, str]:
    """Pull a leading ``Subject:`` line out of a drafted email body."""
    lines = content.splitlines()
    for i, line in enumerate(lines[:5]):
        m = _SUBJECT_RE.match(line)
        if m:
            subject = m.group(1).strip()
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return subject, body or content
    return fallback_subject, content


@router.get("/status", response_model=GmailStatus)
async def gmail_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GmailStatus:
    """Report the mailbox this user would actually send from.

    Provider-aware: a user with their own Gmail connection sees that, not the shared
    SMTP fallback's state.
    """
    status = await integration_service.status(db, user.id)
    # This endpoint answers "will my email actually send?", so anything that cannot
    # send is offline. The exception is a connection needing repair, which is the one
    # state the user can act on. Deployment-level detail belongs on the integrations
    # endpoint, not here.
    mode = status.mode if status.mode in ("live", "needs_reconnect") else "offline"
    return GmailStatus(
        configured=status.can_send,
        address=status.address or None,
        mode=mode,
        provider=status.provider,
        detail=status.detail,
    )


@router.get("", response_model=list[EmailMessageOut])
async def list_emails(
    opportunity_id: int | None = None,
    supplier_lead_id: int | None = None,
    buyer_lead_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[EmailMessageOut]:
    stmt = select(EmailMessage).order_by(EmailMessage.created_at.desc())
    if opportunity_id is not None:
        stmt = stmt.where(EmailMessage.opportunity_id == opportunity_id)
    if supplier_lead_id is not None:
        stmt = stmt.where(EmailMessage.supplier_lead_id == supplier_lead_id)
    if buyer_lead_id is not None:
        stmt = stmt.where(EmailMessage.buyer_lead_id == buyer_lead_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [EmailMessageOut.model_validate(r) for r in rows]


@router.post("/send", response_model=EmailMessageOut, status_code=201)
async def send_email(
    payload: EmailSendRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailMessageOut:
    in_reply_to = None
    references: list[str] = []
    if payload.in_reply_to_message_id is not None:
        parent = await db.get(EmailMessage, payload.in_reply_to_message_id)
        if parent is not None and parent.message_id:
            in_reply_to = parent.message_id
            references = [parent.message_id]

    msg = await email_service.send_email(
        db,
        to_email=str(payload.to_email),
        subject=payload.subject,
        body=payload.body,
        user_id=user.id,
        opportunity_id=payload.opportunity_id,
        supplier_lead_id=payload.supplier_lead_id,
        buyer_lead_id=payload.buyer_lead_id,
        deal_id=payload.deal_id,
        document_id=payload.document_id,
        in_reply_to=in_reply_to,
        references=references,
    )
    if msg.status == "failed":
        raise HTTPException(status_code=502, detail=msg.error or "Email send failed")
    return EmailMessageOut.model_validate(msg)


@router.post("/send-document", response_model=EmailMessageOut, status_code=201)
async def send_document(
    payload: SendDocumentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EmailMessageOut:
    doc = await db.get(Document, payload.document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    # Resolve recipient: explicit > lead email > document supplier block.
    to_email = str(payload.to_email) if payload.to_email else None
    supplier_lead = (
        await db.get(SupplierLead, payload.supplier_lead_id)
        if payload.supplier_lead_id
        else None
    )
    buyer_lead = (
        await db.get(BuyerLead, payload.buyer_lead_id)
        if payload.buyer_lead_id
        else None
    )
    if to_email is None and supplier_lead is not None:
        to_email = supplier_lead.email
    if to_email is None and buyer_lead is not None:
        to_email = buyer_lead.email
    if to_email is None:
        inputs = doc.inputs or {}
        supplier_block = inputs.get("supplier") if isinstance(inputs, dict) else None
        if isinstance(supplier_block, dict):
            to_email = supplier_block.get("email")
    if not to_email:
        raise HTTPException(
            status_code=400,
            detail="No recipient email — pass to_email or a lead with an email.",
        )

    subject, body = _split_subject_body(doc.content, payload.subject or doc.title)

    msg = await email_service.send_email(
        db,
        to_email=to_email,
        subject=subject,
        body=body,
        user_id=user.id,
        opportunity_id=payload.opportunity_id,
        supplier_lead_id=payload.supplier_lead_id,
        buyer_lead_id=payload.buyer_lead_id,
        deal_id=payload.deal_id or doc.deal_id,
        document_id=doc.id,
    )
    if msg.status == "failed":
        raise HTTPException(status_code=502, detail=msg.error or "Email send failed")
    return EmailMessageOut.model_validate(msg)


@router.post("/sync", response_model=ReplySyncResult)
async def sync_replies(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReplySyncResult:
    new_messages, fetched, mode = await email_service.sync_replies(db, user_id=user.id)
    matched = sum(1 for m in new_messages if m.matched_side is not None)
    return ReplySyncResult(
        fetched=fetched,
        matched=matched,
        new_messages=[EmailMessageOut.model_validate(m) for m in new_messages],
        mode=mode,
    )
