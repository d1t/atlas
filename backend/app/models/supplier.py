from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    type: Mapped[str | None] = mapped_column(String(32))  # mill | trader | broker | unknown
    country: Mapped[str | None] = mapped_column(String(128), index=True)
    commodity: Mapped[str | None] = mapped_column(String(128), index=True)
    website: Mapped[str | None] = mapped_column(String(512))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    contact_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(255))

    credibility_score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    red_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
