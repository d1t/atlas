"""V2 opportunity-centric data model.

Transforms the system from "1 deal = 1 supplier = 1 buyer" to
"1 Opportunity -> many SupplierLead + many BuyerLead -> Deal (derived)".

Deals remain the execution record (derived, created only when a supplier-buyer
pair is matched), but the primary orchestration entity moves up one level so
the user can manage multiple candidate suppliers and buyers per trade idea.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

OPPORTUNITY_STATUSES = [
    "draft",
    "sourcing",
    "negotiating",
    "matched",
    "closed",
    "lost",
]

SUPPLIER_LEAD_STATUSES = [
    "new",
    "contacted",
    "quoted",
    "shortlisted",
    "declined",
    "lost",
]

BUYER_LEAD_STATUSES = [
    "new",
    "contacted",
    "engaged",
    "committed",
    "declined",
    "lost",
]

APPETITE_LEVELS = ["low", "medium", "high"]
URGENCY_LEVELS = ["low", "medium", "high"]


class Opportunity(Base, TimestampMixin):
    """A trade idea or mandate the user is working on.

    Example: "50,000 MT raw sugar (ICUMSA 600) to Nigeria, target $480-510/MT CFR".
    """

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    commodity: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    volume_mt: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    destination_country: Mapped[str | None] = mapped_column(String(128))
    destination_port: Mapped[str | None] = mapped_column(String(128))
    incoterms: Mapped[str | None] = mapped_column(String(16))

    target_price_min: Mapped[float | None] = mapped_column(Float)
    target_price_max: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), default="draft", index=True, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class SupplierLead(Base, TimestampMixin):
    """One candidate supplier attached to an opportunity, with their quote and terms.

    Can optionally reference an existing row in the ``suppliers`` (counterparty)
    table when the lead came from the AI discovery flow; otherwise the name,
    country, and email are stored inline so a lead can be created directly from
    an email reply without needing to go through discovery first.
    """

    __tablename__ = "supplier_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL")
    )

    # Inline fallback fields (used when supplier_id is null).
    supplier_name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    contact_title: Mapped[str | None] = mapped_column(String(255))

    # Their quote to us.
    price_mt: Mapped[float | None] = mapped_column(Float)
    quoted_incoterms: Mapped[str | None] = mapped_column(String(16))  # FOB / CFR / CIF
    min_order_mt: Mapped[float | None] = mapped_column(Float)
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    payment_terms: Mapped[str | None] = mapped_column(String(128))

    # Soft signals that feed the matching engine and health score.
    credibility_score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    responsiveness_score: Mapped[int] = mapped_column(
        Integer, default=50, nullable=False
    )
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(
        String(32), default="new", index=True, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    # Negotiation strategy state. ``negotiation_stage`` is the 1-5 bargaining
    # stage from :mod:`app.ai.negotiation_strategy`. ``intel`` is a running
    # dossier of what we've learned about this counterparty (their claimed
    # origin, last quoted price, accepted payment instruments, etc.), and
    # ``disclosed`` is the audit trail of what we've told them — both are
    # free-form JSON so we can add signals without migrations.
    negotiation_stage: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    intel: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    disclosed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class BuyerLead(Base, TimestampMixin):
    """One candidate buyer attached to an opportunity."""

    __tablename__ = "buyer_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # We reuse the ``suppliers`` (counterparty) table for buyers too — it has the
    # right shape for any trading counterparty. Nullable because a buyer lead can
    # be created inline before being promoted to a full counterparty record.
    buyer_id: Mapped[int | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL")
    )

    buyer_name: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str | None] = mapped_column(String(255))

    target_price_mt: Mapped[float | None] = mapped_column(Float)
    volume_mt: Mapped[float | None] = mapped_column(Float)
    appetite: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)

    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(32), default="new", index=True, nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    # Mirrors of SupplierLead negotiation state. See that model for semantics.
    negotiation_stage: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    intel: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    disclosed: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
