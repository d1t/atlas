"""Email log — every outbound/inbound message Atlas sends or ingests via Gmail.

An ``EmailMessage`` is the durable record of a touch in the value chain. It is
linked (all optional) to the ``Opportunity`` + the specific ``SupplierLead`` /
``BuyerLead`` it concerns, plus the ``Document`` it was generated from, so the
opportunity workspace can show a real communication timeline and the reply-sync
job can advance the right lead's negotiation state.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

EMAIL_DIRECTIONS = ["outbound", "inbound"]
# outbound: draft (composed, not sent) | sent | offline (recorded, creds absent) | failed
# inbound:  received
EMAIL_STATUSES = ["draft", "sent", "offline", "failed", "received"]


class EmailMessage(Base, TimestampMixin):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    direction: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)

    # Relationship links (all nullable — a message may pre-date lead promotion).
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL"), index=True
    )
    supplier_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_leads.id", ondelete="SET NULL"), index=True
    )
    buyer_lead_id: Mapped[int | None] = mapped_column(
        ForeignKey("buyer_leads.id", ondelete="SET NULL"), index=True
    )
    deal_id: Mapped[int | None] = mapped_column(
        ForeignKey("deals.id", ondelete="SET NULL")
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    to_email: Mapped[str | None] = mapped_column(String(255))
    from_email: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # RFC 2822 threading headers so replies can be stitched to their parent.
    message_id: Mapped[str | None] = mapped_column(String(512), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(512))

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    error: Mapped[str | None] = mapped_column(Text)
    # Which side an inbound message was matched to ("supplier" | "buyer" | None).
    matched_side: Mapped[str | None] = mapped_column(String(16))
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Convenience for inbound sync idempotency.
    stage_at_send: Mapped[int | None] = mapped_column(Integer)
