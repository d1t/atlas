"""Email service — send outreach/negotiation emails via Gmail and sync replies
back onto the leads they concern.

This is the bridge between the negotiation engine (which drafts the emails) and
the Gmail integration (which transmits them). Sending an email records an
``EmailMessage``, stamps ``last_contacted_at`` and advances the lead status;
syncing replies matches inbound mail to a lead by sender address, logs it, and
folds the reply into the lead's ``intel`` dossier so the next stage-aware draft
has fresh context.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.email_provider import EmailProvider
from app.models.activity import Activity
from app.models.email import EmailMessage
from app.models.opportunity import BuyerLead, SupplierLead

logger = logging.getLogger("atlas.email")


async def resolve_provider(db: AsyncSession, user_id: int | None) -> EmailProvider:
    """Pick the mailbox for this user.

    Imported lazily because the integration service imports the email models this
    module defines behaviour around; deferring keeps the two from circling.
    """
    from app.services import integration_service

    return await integration_service.provider_for(db, user_id)


async def send_email(
    db: AsyncSession,
    *,
    to_email: str,
    subject: str,
    body: str,
    user_id: int | None = None,
    opportunity_id: int | None = None,
    supplier_lead_id: int | None = None,
    buyer_lead_id: int | None = None,
    deal_id: int | None = None,
    document_id: int | None = None,
    in_reply_to: str | None = None,
    references: list[str] | None = None,
    client: EmailProvider | None = None,
) -> EmailMessage:
    """Send an email (or record it offline) and log it against the lead.

    The mailbox is resolved per user rather than fixed: a user with their own Gmail
    connection sends from it, everyone else falls back to the shared SMTP/IMAP client,
    which is itself offline-safe. This function does not know which it got.
    """
    client = client or await resolve_provider(db, user_id)
    result = await client.send(
        to_email,
        subject,
        body,
        in_reply_to=in_reply_to,
        references=references,
    )

    now = datetime.now(UTC)
    msg = EmailMessage(
        direction="outbound",
        status=result.status,
        opportunity_id=opportunity_id,
        supplier_lead_id=supplier_lead_id,
        buyer_lead_id=buyer_lead_id,
        deal_id=deal_id,
        document_id=document_id,
        user_id=user_id,
        to_email=to_email,
        from_email=client.address or None,
        subject=subject,
        body=body,
        message_id=result.message_id,
        in_reply_to=in_reply_to,
        sent_at=now if result.ok else None,
        error=result.error,
    )
    db.add(msg)

    # Advance lead state on a successful (or offline-recorded) send.
    if result.ok:
        lead: SupplierLead | BuyerLead | None = None
        if supplier_lead_id is not None:
            lead = await db.get(SupplierLead, supplier_lead_id)
        elif buyer_lead_id is not None:
            lead = await db.get(BuyerLead, buyer_lead_id)
        if lead is not None:
            lead.last_contacted_at = now
            msg.stage_at_send = lead.negotiation_stage
            if lead.status == "new":
                lead.status = "contacted"
            disclosed = dict(lead.disclosed or {})
            log = list(disclosed.get("email_log", []))
            log.append({"at": now.isoformat(), "subject": subject, "status": result.status})
            disclosed["email_log"] = log[-25:]
            lead.disclosed = disclosed

    db.add(
        Activity(
            deal_id=deal_id,
            user_id=user_id,
            type="email",
            message=(
                f"{'Sent' if result.status == 'sent' else result.status.title()} "
                f"email to {to_email}: {subject}"
            ),
        )
    )

    await db.commit()
    await db.refresh(msg)
    return msg


def _bump_score(current: int, delta: int) -> int:
    return max(0, min(100, current + delta))


async def sync_replies(
    db: AsyncSession,
    *,
    since: datetime | None = None,
    user_id: int | None = None,
    client: EmailProvider | None = None,
) -> tuple[list[EmailMessage], int, str]:
    """Pull inbound mail, match to leads by sender address, and log new replies.

    Returns ``(new_messages, fetched_count, mode)`` where ``mode`` is
    ``"live"`` or ``"offline"``.
    """
    client = client or await resolve_provider(db, user_id)
    mode = "live" if client.configured else "offline"
    fetched = await client.fetch_replies(since=since)

    new_messages: list[EmailMessage] = []
    for item in fetched:
        if not item.from_email:
            continue
        # Idempotency: skip messages we've already ingested by Message-ID.
        if item.message_id:
            existing = (
                await db.execute(
                    select(EmailMessage.id).where(
                        EmailMessage.message_id == item.message_id,
                        EmailMessage.direction == "inbound",
                    )
                )
            ).first()
            if existing is not None:
                continue

        supplier_lead = (
            await db.execute(
                select(SupplierLead)
                .where(SupplierLead.email.ilike(item.from_email))
                .order_by(SupplierLead.created_at.desc())
            )
        ).scalars().first()
        buyer_lead = None
        if supplier_lead is None:
            buyer_lead = (
                await db.execute(
                    select(BuyerLead)
                    .where(BuyerLead.email.ilike(item.from_email))
                    .order_by(BuyerLead.created_at.desc())
                )
            ).scalars().first()

        lead = supplier_lead or buyer_lead
        matched_side = "supplier" if supplier_lead else "buyer" if buyer_lead else None

        msg = EmailMessage(
            direction="inbound",
            status="received",
            opportunity_id=lead.opportunity_id if lead is not None else None,
            supplier_lead_id=supplier_lead.id if supplier_lead is not None else None,
            buyer_lead_id=buyer_lead.id if buyer_lead is not None else None,
            from_email=item.from_email,
            to_email=client.address or None,
            subject=item.subject,
            body=item.body,
            message_id=item.message_id or None,
            in_reply_to=item.in_reply_to,
            received_at=item.received_at,
            matched_side=matched_side,
        )
        db.add(msg)
        new_messages.append(msg)

        if lead is not None:
            # Fold the reply into the lead's running dossier so the next
            # stage-aware draft addresses their actual words.
            intel = dict(lead.intel or {})
            intel["last_supplier_response"] = item.body[:4000]
            intel["last_reply_at"] = (
                item.received_at.isoformat() if item.received_at else datetime.now(UTC).isoformat()
            )
            lead.intel = intel
            lead.responsiveness_score = _bump_score(lead.responsiveness_score, 15)
            # A reply means they're engaging — advance an untouched/contacted lead.
            if lead.status in ("new", "contacted"):
                lead.status = "quoted" if supplier_lead is not None else "engaged"

    await db.commit()
    for m in new_messages:
        await db.refresh(m)
    return new_messages, len(fetched), mode
