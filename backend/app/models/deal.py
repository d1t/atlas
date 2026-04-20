from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

DEAL_STAGES = [
    "lead",
    "contacted",
    "qualified",
    "pricing",
    "buyer_matched",
    "spa",
    "lc",
    "shipment",
    "closed",
    "lost",
]


class Deal(Base, TimestampMixin):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    commodity: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    volume_mt: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    buy_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sell_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    freight_estimate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    incoterms: Mapped[str | None] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)

    stage: Mapped[str] = mapped_column(String(32), default="lead", index=True, nullable=False)
    structure: Mapped[str | None] = mapped_column(String(32))  # principal / brokerage / b2b_lc

    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    buyer_id: Mapped[int | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    notes: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    margin_per_mt: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_margin: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    probability: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
