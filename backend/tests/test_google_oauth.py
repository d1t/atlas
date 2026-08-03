"""Google OAuth tests against a mocked Google.

Google is replaced with a fake that behaves like the real one in the ways that matter:
it issues a refresh token only when consent is forced, returns whatever scopes the user
actually granted, and distinguishes a temporarily failed refresh from a permanently
dead one. That last distinction is the difference between retrying and telling the user
to reconnect.
"""
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import crypto
from app.core.config import Settings
from app.integrations.gmail_oauth import GmailOAuthProvider
from app.integrations.google_oauth import (
    READ_SCOPE,
    SEND_SCOPE,
    OAuthError,
    ProfileInfo,
    TokenRefreshError,
    TokenSet,
    missing_scopes,
    scope_explanations,
)
from app.models import Base
from app.models.integration import GmailConnection, OAuthState
from app.models.user import User
from app.services import integration_service

GRANTED = f"{SEND_SCOPE} {READ_SCOPE} openid email"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gmail_oauth_enabled=True,
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_redirect_uri="http://localhost:8000/api/v1/integrations/google/callback",
        token_encryption_key=Fernet.generate_key().decode(),
        gmail_address="",
        gmail_app_password="",
    )


@pytest.fixture(autouse=True)
def _crypto_key(settings, monkeypatch):
    """Point the token cipher at a per-test key."""
    monkeypatch.setattr("app.core.crypto.get_settings", lambda: settings)
    crypto._cipher.cache_clear()
    yield
    crypto._cipher.cache_clear()


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oauth.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(
            User(
                id=1,
                email="dapo@atlas.example.com",
                hashed_password="x",
                role="trader",
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


class FakeGoogle:
    """Stands in for Google. Records what it was asked, controls what it returns."""

    def __init__(self, settings: Settings, **behaviour) -> None:
        self.settings = settings
        self.configured = behaviour.get("configured", True)
        self.granted = behaviour.get("granted", GRANTED)
        self.refresh_error: TokenRefreshError | None = behaviour.get("refresh_error")
        self.issue_refresh_token = behaviour.get("issue_refresh_token", True)
        self.rotate_refresh_token = behaviour.get("rotate_refresh_token", False)
        self.send_error: OAuthError | None = behaviour.get("send_error")
        self.revoked: list[str] = []
        self.sent: list[dict] = []
        self.refreshes = 0

    def authorization_url(self, state: str) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}"

    async def exchange_code(self, code: str) -> TokenSet:
        if code == "bad-code":
            raise OAuthError("Could not complete the connection (Google returned 400).")
        return TokenSet(
            access_token="access-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            refresh_token="refresh-1" if self.issue_refresh_token else None,
            scopes=tuple(self.granted.split()),
        )

    async def refresh(self, refresh_token: str) -> TokenSet:
        self.refreshes += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        return TokenSet(
            access_token=f"access-{self.refreshes + 1}",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            refresh_token="refresh-2" if self.rotate_refresh_token else None,
            scopes=tuple(self.granted.split()),
        )

    async def revoke(self, token: str) -> None:
        self.revoked.append(token)

    async def fetch_profile(self, access_token: str) -> ProfileInfo:
        return ProfileInfo(email="dapo@atlas.example.com")

    async def send_message(self, access_token: str, **kw) -> str:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append({"access_token": access_token, **kw})
        return "thread-1"


async def _connect(session, google, *, code="good-code") -> GmailConnection:
    url = await integration_service.begin_authorization(session, 1, client=google)
    state = url.split("state=")[1]
    return await integration_service.complete_authorization(
        session, state=state, code=code, client=google
    )


# --- Scope minimalism -------------------------------------------------------


def test_only_the_two_gmail_scopes_are_requested():
    """A broader grant is harder to justify and harder to get verified."""
    scopes = {e["scope"] for e in scope_explanations()}
    assert SEND_SCOPE in scopes
    assert READ_SCOPE in scopes
    assert "https://www.googleapis.com/auth/gmail.modify" not in scopes
    assert "https://mail.google.com/" not in scopes


def test_every_requested_scope_is_explained_in_plain_language():
    for entry in scope_explanations():
        assert entry["reason"] and not entry["reason"].startswith("http")


# --- Connect ----------------------------------------------------------------


async def test_connect_stores_an_encrypted_refresh_token(session, settings):
    conn = await _connect(session, FakeGoogle(settings))
    await session.commit()

    assert conn.connected and conn.fault is None
    assert conn.email == "dapo@atlas.example.com"
    assert conn.refresh_token_enc and "refresh-1" not in conn.refresh_token_enc
    assert crypto.decrypt(conn.refresh_token_enc) == "refresh-1"


async def test_connect_is_refused_when_google_is_not_configured(session, settings):
    google = FakeGoogle(settings, configured=False)
    with pytest.raises(integration_service.IntegrationUnavailable):
        await integration_service.begin_authorization(session, 1, client=google)


async def test_connect_is_refused_when_tokens_could_not_be_protected(
    session, settings, monkeypatch
):
    """Better to block the flow than to store a mailbox key in plaintext."""
    monkeypatch.setattr(
        "app.core.crypto.get_settings",
        lambda: Settings(**{**settings.model_dump(), "token_encryption_key": ""}),
    )
    crypto._cipher.cache_clear()
    with pytest.raises(crypto.TokenEncryptionUnavailable):
        await integration_service.begin_authorization(
            session, 1, client=FakeGoogle(settings)
        )


# --- Callback integrity -----------------------------------------------------


async def test_a_forged_callback_state_is_rejected(session, settings):
    with pytest.raises(OAuthError, match="no longer valid"):
        await integration_service.complete_authorization(
            session, state="made-up", code="good-code", client=FakeGoogle(settings)
        )


async def test_a_replayed_callback_is_rejected(session, settings):
    """State is single-use, so a captured callback cannot be used twice."""
    google = FakeGoogle(settings)
    url = await integration_service.begin_authorization(session, 1, client=google)
    state = url.split("state=")[1]
    await integration_service.complete_authorization(
        session, state=state, code="good-code", client=google
    )
    with pytest.raises(OAuthError, match="no longer valid"):
        await integration_service.complete_authorization(
            session, state=state, code="good-code", client=google
        )


async def test_an_expired_authorization_attempt_is_rejected(session, settings):
    google = FakeGoogle(settings)
    url = await integration_service.begin_authorization(session, 1, client=google)
    state = url.split("state=")[1]
    record = (
        await session.execute(select(OAuthState).where(OAuthState.state == state))
    ).scalars().one()
    record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.flush()

    with pytest.raises(OAuthError, match="timed out"):
        await integration_service.complete_authorization(
            session, state=state, code="good-code", client=google
        )


async def test_a_failed_code_exchange_does_not_create_a_connection(session, settings):
    google = FakeGoogle(settings)
    with pytest.raises(OAuthError):
        await _connect(session, google, code="bad-code")
    assert await integration_service.get_connection(session, 1) is None


# --- Partial consent --------------------------------------------------------


async def test_declining_the_send_permission_is_detected_at_connect(session, settings):
    """Google lets a user untick a scope; that must not surface later as a 403."""
    google = FakeGoogle(settings, granted=f"{READ_SCOPE} openid email")
    conn = await _connect(session, google)
    assert conn.fault == "scope_changed"

    status = await integration_service.status(session, 1, settings=settings)
    assert status.mode == "needs_reconnect"
    assert SEND_SCOPE in status.missing_scopes
    assert not status.can_send


async def test_a_send_without_permission_fails_with_an_actionable_message(
    session, settings
):
    google = FakeGoogle(settings, granted=f"{READ_SCOPE} openid email")
    conn = await _connect(session, google)
    provider = GmailOAuthProvider(session, conn, client=google, settings=settings)

    result = await provider.send("buyer@example.com", "Hi", "Body")
    assert result.status == "failed"
    assert "Reconnect and allow sending" in result.error
    assert google.sent == []


def test_missing_scopes_ignores_extras_google_adds():
    assert missing_scopes(f"{SEND_SCOPE} {READ_SCOPE} openid email profile") == []


# --- Token refresh ----------------------------------------------------------


async def test_an_expired_access_token_is_refreshed_transparently(session, settings):
    google = FakeGoogle(settings)
    conn = await _connect(session, google)
    conn.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    provider = GmailOAuthProvider(session, conn, client=google, settings=settings)
    result = await provider.send("buyer@example.com", "Hi", "Body")

    assert result.status == "sent"
    assert google.refreshes == 1
    assert google.sent[0]["access_token"] == "access-2"
    assert conn.last_refreshed_at is not None


async def test_a_valid_access_token_is_not_refreshed(session, settings):
    google = FakeGoogle(settings)
    conn = await _connect(session, google)
    provider = GmailOAuthProvider(session, conn, client=google, settings=settings)

    await provider.send("buyer@example.com", "Hi", "Body")
    assert google.refreshes == 0


async def test_a_rotated_refresh_token_replaces_the_stored_one(session, settings):
    google = FakeGoogle(settings, rotate_refresh_token=True)
    conn = await _connect(session, google)
    conn.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    await GmailOAuthProvider(session, conn, client=google, settings=settings).send(
        "buyer@example.com", "Hi", "Body"
    )
    assert crypto.decrypt(conn.refresh_token_enc) == "refresh-2"


async def test_a_refresh_that_omits_a_new_token_keeps_the_old_one(session, settings):
    """Google usually does not rotate. Overwriting with None would break the account."""
    google = FakeGoogle(settings)
    conn = await _connect(session, google)
    conn.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    await GmailOAuthProvider(session, conn, client=google, settings=settings).send(
        "buyer@example.com", "Hi", "Body"
    )
    assert crypto.decrypt(conn.refresh_token_enc) == "refresh-1"


async def test_a_revoked_grant_marks_the_connection_for_reauthentication(
    session, settings
):
    google = FakeGoogle(
        settings,
        refresh_error=TokenRefreshError("invalid_grant", requires_reconnect=True),
    )
    conn = await _connect(session, google)
    conn.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    result = await GmailOAuthProvider(
        session, conn, client=google, settings=settings
    ).send("buyer@example.com", "Hi", "Body")

    assert result.status == "failed"
    assert conn.fault == "revoked"
    assert conn.connected is False

    status = await integration_service.status(session, 1, settings=settings)
    assert status.mode == "needs_reconnect"
    assert not status.can_send


async def test_a_transient_refresh_failure_does_not_force_reauthentication(
    session, settings
):
    """A 503 from Google is not the user's problem to solve by reconnecting."""
    google = FakeGoogle(
        settings,
        refresh_error=TokenRefreshError("temporarily unavailable"),
    )
    conn = await _connect(session, google)
    conn.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    await GmailOAuthProvider(session, conn, client=google, settings=settings).send(
        "buyer@example.com", "Hi", "Body"
    )
    assert conn.fault == "error"
    assert conn.connected is True


async def test_a_connection_with_no_refresh_token_asks_the_user_to_reconnect(
    session, settings
):
    google = FakeGoogle(settings, issue_refresh_token=False)
    conn = await _connect(session, google)
    conn.access_token_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    result = await GmailOAuthProvider(
        session, conn, client=google, settings=settings
    ).send("buyer@example.com", "Hi", "Body")
    assert result.status == "failed"
    assert conn.fault == "expired"


# --- Reconnect and disconnect ------------------------------------------------


async def test_reconnecting_replaces_the_connection_without_duplicating_it(
    session, settings
):
    google = FakeGoogle(settings)
    first = await _connect(session, google)
    first.fault = "revoked"
    first.connected = False
    await session.flush()

    second = await _connect(session, google)
    await session.commit()

    assert second.id == first.id
    assert second.connected and second.fault is None
    rows = (await session.execute(select(GmailConnection))).scalars().all()
    assert len(rows) == 1


async def test_disconnect_revokes_at_google_and_deletes_the_tokens(session, settings):
    google = FakeGoogle(settings)
    await _connect(session, google)
    await session.commit()

    assert await integration_service.disconnect(session, 1, client=google) is True
    await session.commit()

    assert google.revoked == ["refresh-1"]
    assert await integration_service.get_connection(session, 1) is None


async def test_disconnect_is_harmless_when_nothing_is_connected(session, settings):
    assert (
        await integration_service.disconnect(session, 1, client=FakeGoogle(settings))
        is False
    )


# --- Status and provider selection -------------------------------------------


async def test_status_is_offline_rather_than_broken_when_nothing_is_connected(
    session, settings
):
    status = await integration_service.status(session, 1, settings=settings)
    assert status.mode == "offline"
    assert not status.can_send
    assert "not sent until you connect" in status.detail


async def test_status_says_unavailable_when_oauth_is_not_deployed(session):
    """The flag being off is a deployment fact, not a user error."""
    status = await integration_service.status(
        session, 1, settings=Settings(gmail_oauth_enabled=False)
    )
    assert status.mode == "unavailable"


async def test_status_labels_the_app_password_path_as_a_fallback(session):
    status = await integration_service.status(
        session,
        1,
        settings=Settings(
            gmail_address="ops@atlas.example.com", gmail_app_password="x" * 16
        ),
    )
    assert status.provider == "smtp"
    assert "fallback" in status.detail


async def test_a_healthy_connection_is_preferred_over_the_shared_mailbox(
    session, settings
):
    await _connect(session, FakeGoogle(settings))
    await session.commit()
    provider = await integration_service.provider_for(session, 1, settings=settings)
    assert isinstance(provider, GmailOAuthProvider)


async def test_execution_falls_back_rather_than_failing_with_no_connection(
    session, settings
):
    """provider_for never returns None, so execution never branches on transport."""
    provider = await integration_service.provider_for(session, 1, settings=settings)
    assert provider.configured is False
    result = await provider.send("buyer@example.com", "Hi", "Body")
    assert result.status == "offline"


async def test_a_faulted_connection_is_not_used_for_sending(session, settings):
    conn = await _connect(session, FakeGoogle(settings))
    conn.fault = "revoked"
    conn.connected = False
    await session.commit()

    provider = await integration_service.provider_for(session, 1, settings=settings)
    assert not isinstance(provider, GmailOAuthProvider)


async def test_the_feature_flag_keeps_oauth_out_of_the_way_when_off(session, settings):
    await _connect(session, FakeGoogle(settings))
    await session.commit()

    off = Settings(**{**settings.model_dump(), "gmail_oauth_enabled": False})
    provider = await integration_service.provider_for(session, 1, settings=off)
    assert not isinstance(provider, GmailOAuthProvider)


# --- Token storage -----------------------------------------------------------


async def test_a_changed_encryption_key_reports_clearly_instead_of_corrupting(
    session, settings, monkeypatch
):
    conn = await _connect(session, FakeGoogle(settings))
    await session.commit()
    stored = conn.refresh_token_enc

    monkeypatch.setattr(
        "app.core.crypto.get_settings",
        lambda: Settings(
            **{**settings.model_dump(), "token_encryption_key": Fernet.generate_key().decode()}
        ),
    )
    crypto._cipher.cache_clear()

    with pytest.raises(crypto.TokenEncryptionUnavailable, match="must reconnect"):
        crypto.decrypt(stored)


# --- Wired into the send path, not decorative ---------------------------------


async def test_the_send_path_uses_the_users_own_mailbox(session, settings, monkeypatch):
    """The abstraction only matters if execution actually goes through it."""
    from app.services import email_service

    google = FakeGoogle(settings)
    await _connect(session, google)
    await session.commit()

    monkeypatch.setattr(
        "app.services.integration_service.get_settings", lambda: settings
    )
    # provider_for builds its own client, so the fake has to be substituted at the
    # class it reaches for.
    monkeypatch.setattr(
        "app.integrations.gmail_oauth.GoogleOAuthClient", lambda _s=None: google
    )

    msg = await email_service.send_email(
        session,
        to_email="buyer@example.com",
        subject="RFQ",
        body="Body",
        user_id=1,
    )

    assert msg.status == "sent"
    assert msg.from_email == "dapo@atlas.example.com"
    assert google.sent[0]["to_email"] == "buyer@example.com"


async def test_a_user_without_a_connection_still_records_offline(session, settings, monkeypatch):
    """No mailbox must degrade to recording, never to an exception."""
    from app.services import email_service

    monkeypatch.setattr(
        "app.services.integration_service.get_settings", lambda: settings
    )
    msg = await email_service.send_email(
        session,
        to_email="buyer@example.com",
        subject="RFQ",
        body="Body",
        user_id=1,
    )
    assert msg.status == "offline"
    assert msg.sent_at is not None
