"""Per-user mailbox connections."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

#: Why a connection is not usable. ``None`` means healthy.
CONNECTION_FAULTS = (
    "revoked",       # the user withdrew access at Google
    "expired",       # refresh failed and cannot be retried without the user
    "scope_changed", # granted scopes no longer cover what Atlas needs
    "error",         # transient or unclassified failure
)


class GmailConnection(Base, TimestampMixin):
    """A single user's OAuth connection to their own Gmail mailbox.

    Tokens are stored encrypted. The access token is short-lived and refreshed
    transparently; the refresh token is the sensitive one and is the reason this table
    never holds plaintext.
    """

    __tablename__ = "gmail_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        unique=True,
        nullable=False,
    )

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(32), default="google", nullable=False
    )

    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    #: Space-separated, exactly as Google returned them — so a scope Atlas needs but
    #: the user declined is detectable rather than surfacing as a 403 mid-send.
    granted_scopes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    connected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fault: Mapped[str | None] = mapped_column(String(32))
    fault_detail: Mapped[str | None] = mapped_column(Text)

    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OAuthState(Base, TimestampMixin):
    """A one-time CSRF token for an in-flight authorisation.

    Single-use and short-lived: a replayed callback must not be able to attach a
    mailbox to the wrong account.
    """

    __tablename__ = "oauth_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), default="google", nullable=False)
    redirect_to: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
