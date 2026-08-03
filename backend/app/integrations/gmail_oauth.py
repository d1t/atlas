"""The OAuth-backed mailbox provider.

Conforms to :class:`~app.integrations.email_provider.EmailProvider`, so execution code
cannot tell it apart from the SMTP/IMAP fallback.

Token refresh happens here rather than at call sites: a caller that has to remember to
check expiry before every send will eventually forget.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings, get_settings
from app.integrations.gmail import FetchedEmail, SendResult
from app.integrations.google_oauth import (
    GoogleOAuthClient,
    OAuthError,
    TokenRefreshError,
    missing_scopes,
)
from app.models.integration import GmailConnection

logger = logging.getLogger("atlas.gmail_oauth")


class GmailOAuthProvider:
    """Sends and reads through one user's own Gmail account."""

    def __init__(
        self,
        db: AsyncSession,
        connection: GmailConnection,
        *,
        client: GoogleOAuthClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.connection = connection
        self.settings = settings or get_settings()
        self.client = client or GoogleOAuthClient(self.settings)

    @property
    def configured(self) -> bool:
        return (
            self.connection.connected
            and self.connection.fault is None
            and not missing_scopes(self.connection.granted_scopes)
        )

    @property
    def address(self) -> str:
        return self.connection.email

    async def _access_token(self) -> str:
        """Return a usable access token, refreshing first if it is close to lapsing."""
        conn = self.connection
        expires = conn.access_token_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        if conn.access_token_enc and expires and expires > datetime.now(UTC):
            return crypto.decrypt(conn.access_token_enc)

        if not conn.refresh_token_enc:
            await self._mark_fault("expired", "No refresh token is stored.")
            raise TokenRefreshError(
                "Your Gmail connection has expired and must be set up again.",
                requires_reconnect=True,
            )

        try:
            tokens = await self.client.refresh(crypto.decrypt(conn.refresh_token_enc))
        except TokenRefreshError as exc:
            await self._mark_fault(
                "revoked" if exc.requires_reconnect else "error", str(exc)
            )
            raise

        conn.access_token_enc = crypto.encrypt(tokens.access_token)
        conn.access_token_expires_at = tokens.expires_at
        conn.last_refreshed_at = datetime.now(UTC)
        # Google only returns a new refresh token when it has rotated one.
        if tokens.refresh_token:
            conn.refresh_token_enc = crypto.encrypt(tokens.refresh_token)
        if tokens.scopes:
            conn.granted_scopes = " ".join(tokens.scopes)
        conn.fault = None
        conn.fault_detail = None
        await self.db.flush()
        return tokens.access_token

    async def _mark_fault(self, fault: str, detail: str) -> None:
        self.connection.fault = fault
        self.connection.fault_detail = detail
        if fault in ("revoked", "expired"):
            self.connection.connected = False
        await self.db.flush()

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        *,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> SendResult:
        message_id = f"<{uuid.uuid4().hex}@atlas.local>"
        gaps = missing_scopes(self.connection.granted_scopes)
        if gaps:
            await self._mark_fault(
                "scope_changed", "Permission to send email was not granted."
            )
            return SendResult(
                message_id=message_id,
                status="failed",
                error=(
                    "Atlas does not have permission to send from this mailbox. "
                    "Reconnect and allow sending."
                ),
            )

        try:
            token = await self._access_token()
            await self.client.send_message(
                token,
                from_email=self.connection.email,
                from_name=self.settings.gmail_from_name,
                to_email=to_email,
                subject=subject,
                body=body,
                message_id=message_id,
                in_reply_to=in_reply_to,
                references=references,
            )
        except OAuthError as exc:
            logger.warning("Gmail OAuth send failed: %s", exc)
            return SendResult(message_id=message_id, status="failed", error=str(exc))

        self.connection.last_used_at = datetime.now(UTC)
        await self.db.flush()
        return SendResult(message_id=message_id, status="sent")

    async def fetch_replies(
        self, *, since: datetime | None = None, limit: int = 50
    ) -> list[FetchedEmail]:
        # Reply ingestion over the Gmail API lands with the execution wiring; until
        # then this returns nothing rather than pretending to have synced.
        return []
