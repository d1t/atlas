"""Connect, inspect, refresh and disconnect a user's mailbox.

Also decides which provider a given user actually gets, which is the seam that lets
strategy execution stay ignorant of how mail is sent.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings, get_settings
from app.integrations.email_provider import EmailProvider, ProviderStatus
from app.integrations.gmail import GmailClient
from app.integrations.gmail_oauth import GmailOAuthProvider
from app.integrations.google_oauth import (
    GoogleOAuthClient,
    OAuthError,
    missing_scopes,
)
from app.models.integration import GmailConnection, OAuthState

#: An authorisation left half-finished should not stay valid indefinitely.
STATE_TTL = timedelta(minutes=15)


class IntegrationUnavailable(RuntimeError):
    """Raised when the OAuth flow cannot be offered at all."""


async def get_connection(db: AsyncSession, user_id: int) -> GmailConnection | None:
    return (
        await db.execute(
            select(GmailConnection).where(GmailConnection.user_id == user_id)
        )
    ).scalars().first()


async def begin_authorization(
    db: AsyncSession,
    user_id: int,
    *,
    client: GoogleOAuthClient | None = None,
    redirect_to: str | None = None,
) -> str:
    """Mint a single-use state token and return the Google consent URL."""
    oauth = client or GoogleOAuthClient()
    if not oauth.configured:
        raise IntegrationUnavailable(
            "Gmail sign-in is not available yet: Google OAuth credentials have not "
            "been configured on this deployment."
        )
    # Checked before sending the user to Google, so a missing key surfaces as a clear
    # error here rather than at the callback with tokens already in hand.
    crypto._cipher()

    state = secrets.token_urlsafe(32)
    db.add(
        OAuthState(
            state=state,
            user_id=user_id,
            provider="google",
            redirect_to=redirect_to,
            expires_at=datetime.now(UTC) + STATE_TTL,
        )
    )
    await db.flush()
    return oauth.authorization_url(state)


async def complete_authorization(
    db: AsyncSession,
    *,
    state: str,
    code: str,
    client: GoogleOAuthClient | None = None,
) -> GmailConnection:
    """Exchange the code and store the connection against the originating user."""
    oauth = client or GoogleOAuthClient()

    record = (
        await db.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalars().first()
    if record is None or record.consumed_at is not None:
        # Covers both a forged callback and a replayed one.
        raise OAuthError("This sign-in link is no longer valid. Please try again.")

    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires < datetime.now(UTC):
        raise OAuthError("This sign-in attempt timed out. Please try again.")

    record.consumed_at = datetime.now(UTC)
    await db.flush()

    tokens = await oauth.exchange_code(code)
    profile = await oauth.fetch_profile(tokens.access_token)

    conn = await get_connection(db, record.user_id)
    if conn is None:
        conn = GmailConnection(user_id=record.user_id, email=profile.email)
        db.add(conn)

    conn.email = profile.email
    conn.provider = "google"
    conn.access_token_enc = crypto.encrypt(tokens.access_token)
    conn.access_token_expires_at = tokens.expires_at
    if tokens.refresh_token:
        conn.refresh_token_enc = crypto.encrypt(tokens.refresh_token)
    conn.granted_scopes = " ".join(tokens.scopes)
    conn.connected = True
    conn.connected_at = datetime.now(UTC)

    # A user can untick permissions on the consent screen. Recording that as a fault
    # now means the UI can say what is wrong instead of failing on first send.
    gaps = missing_scopes(conn.granted_scopes)
    if gaps:
        conn.fault = "scope_changed"
        conn.fault_detail = (
            "Sending permission was not granted." if len(gaps) == 1
            else "Required permissions were not granted."
        )
    else:
        conn.fault = None
        conn.fault_detail = None

    await db.flush()
    return conn


async def disconnect(
    db: AsyncSession, user_id: int, *, client: GoogleOAuthClient | None = None
) -> bool:
    """Withdraw the grant at Google and drop the stored tokens."""
    conn = await get_connection(db, user_id)
    if conn is None:
        return False

    if conn.refresh_token_enc:
        try:
            await (client or GoogleOAuthClient()).revoke(
                crypto.decrypt(conn.refresh_token_enc)
            )
        except crypto.TokenEncryptionUnavailable:
            # An undecryptable token cannot be revoked, but must still be deleted.
            pass

    await db.delete(conn)
    await db.flush()
    return True


async def status(
    db: AsyncSession, user_id: int, *, settings: Settings | None = None
) -> ProviderStatus:
    """Describe the user's mailbox situation without hiding a degraded state."""
    s = settings or get_settings()
    conn = await get_connection(db, user_id)

    if conn is not None:
        gaps = tuple(missing_scopes(conn.granted_scopes))
        if not conn.connected or conn.fault in ("revoked", "expired"):
            return ProviderStatus(
                provider="google",
                connected=False,
                mode="needs_reconnect",
                address=conn.email,
                fault=conn.fault,
                detail=conn.fault_detail
                or "Your Gmail connection needs to be set up again.",
                connected_at=conn.connected_at,
                last_used_at=conn.last_used_at,
            )
        if gaps:
            return ProviderStatus(
                provider="google",
                connected=True,
                mode="needs_reconnect",
                address=conn.email,
                fault="scope_changed",
                detail=(
                    "Atlas is missing a permission it needs. Reconnect and accept all "
                    "requested permissions."
                ),
                missing_scopes=gaps,
                connected_at=conn.connected_at,
                last_used_at=conn.last_used_at,
            )
        return ProviderStatus(
            provider="google",
            connected=True,
            mode="live",
            address=conn.email,
            detail=f"Sending as {conn.email}.",
            connected_at=conn.connected_at,
            last_used_at=conn.last_used_at,
        )

    if s.gmail_configured:
        return ProviderStatus(
            provider="smtp",
            connected=True,
            mode="live",
            address=s.gmail_address,
            detail=(
                "Using the shared SMTP/IMAP mailbox. This is a development and "
                "administrator fallback, not a per-user connection."
            ),
        )

    if not s.google_oauth_configured:
        return ProviderStatus(
            provider="google",
            connected=False,
            mode="unavailable",
            detail=(
                "Gmail sign-in is not configured on this deployment. Outbound emails "
                "are recorded but not sent."
            ),
        )

    return ProviderStatus(
        provider="google",
        connected=False,
        mode="offline",
        detail=(
            "No mailbox connected. Emails are drafted and recorded but not sent until "
            "you connect Gmail."
        ),
    )


async def provider_for(
    db: AsyncSession, user_id: int | None, *, settings: Settings | None = None
) -> EmailProvider:
    """Return the mailbox to use for this user.

    A healthy per-user OAuth connection wins. Otherwise the shared SMTP/IMAP client,
    which is itself offline-safe when unconfigured — so this never returns ``None`` and
    callers never branch on transport.
    """
    s = settings or get_settings()
    if user_id is not None and s.gmail_oauth_enabled:
        conn = await get_connection(db, user_id)
        if conn is not None and conn.connected and conn.fault is None:
            return GmailOAuthProvider(db, conn, settings=s)
    return GmailClient(s)
