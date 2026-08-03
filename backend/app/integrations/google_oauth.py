"""Google OAuth 2.0 and the Gmail REST API.

Spoken directly over HTTP with ``httpx`` rather than through Google's SDK: the two
endpoints and two API calls Atlas needs are small, and the SDK pulls a large dependency
tree plus its own auth-state handling that would fight the token store here.

Only two scopes are requested. Nothing in the current workflows mutates or deletes
mail, so ``gmail.modify`` and full ``mail.google.com`` are deliberately not asked for —
a broader grant is harder to justify to a user and harder to get through Google's
verification later.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage as MimeMessage

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger("atlas.google_oauth")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"

SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

#: The minimum that makes the product work: send approved outreach, and read replies to
#: detect them. Ordered so the consent screen reads sensibly.
REQUIRED_SCOPES = (SEND_SCOPE, READ_SCOPE, "openid", "email")

#: Plain-language reasons, shown before the user is sent to Google. A consent screen
#: full of unexplained scopes is how people click through without understanding.
SCOPE_EXPLANATIONS: dict[str, str] = {
    SEND_SCOPE: "Send the outreach emails you have approved, from your own address.",
    READ_SCOPE: (
        "Read replies so Atlas can tell when a counterparty has responded. Atlas never "
        "modifies or deletes anything in your mailbox."
    ),
    "openid": "Confirm which Google account you connected.",
    "email": "Record the address Atlas will be sending from.",
}


class OAuthError(RuntimeError):
    """A failure in the OAuth exchange, with a message safe to show a user."""


class TokenRefreshError(OAuthError):
    """Refresh failed. If ``requires_reconnect`` the user must authorise again."""

    def __init__(self, message: str, *, requires_reconnect: bool = False) -> None:
        super().__init__(message)
        self.requires_reconnect = requires_reconnect


@dataclass
class TokenSet:
    access_token: str
    expires_at: datetime
    refresh_token: str | None = None
    scopes: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ProfileInfo:
    email: str


def scope_explanations(scopes: tuple[str, ...] = REQUIRED_SCOPES) -> list[dict[str, str]]:
    return [
        {"scope": s, "reason": SCOPE_EXPLANATIONS.get(s, "Required by Google.")}
        for s in scopes
    ]


def missing_scopes(granted: str) -> list[str]:
    """Scopes Atlas needs that the user did not grant.

    Google lets a user untick individual permissions. Detecting that here turns a
    confusing 403 during a send into an explicit "reconnect and allow sending".
    """
    have = set(granted.split())
    return [s for s in (SEND_SCOPE, READ_SCOPE) if s not in have]


class GoogleOAuthClient:
    """Talks to Google. Holds no per-user state."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return self.settings.google_oauth_configured

    def authorization_url(self, state: str) -> str:
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_SCOPES),
            # offline + consent so a refresh token is actually issued. Google omits it
            # on re-authorisation otherwise, which silently breaks reconnect.
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return str(httpx.URL(AUTH_ENDPOINT, params=params))

    async def exchange_code(self, code: str) -> TokenSet:
        payload = {
            "code": code,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "redirect_uri": self.settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(TOKEN_ENDPOINT, data=payload)
        if resp.status_code != 200:
            raise OAuthError(_google_error(resp, "Could not complete the connection"))
        return _token_set(resp.json())

    async def refresh(self, refresh_token: str) -> TokenSet:
        payload = {
            "refresh_token": refresh_token,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(TOKEN_ENDPOINT, data=payload)
        if resp.status_code != 200:
            body = resp.json() if _is_json(resp) else {}
            # invalid_grant means the token is dead for good — revoked, expired after
            # long disuse, or the password changed. Retrying cannot fix it.
            terminal = body.get("error") == "invalid_grant"
            raise TokenRefreshError(
                _google_error(resp, "Could not refresh access to your mailbox"),
                requires_reconnect=terminal,
            )
        return _token_set(resp.json())

    async def revoke(self, token: str) -> None:
        """Withdraw the grant at Google. Best effort: already-invalid is success."""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                await client.post(REVOKE_ENDPOINT, data={"token": token})
        except httpx.HTTPError:
            logger.warning("Token revocation call failed; disconnecting locally anyway")

    async def fetch_profile(self, access_token: str) -> ProfileInfo:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code != 200:
            raise OAuthError(_google_error(resp, "Could not read your Google profile"))
        email = resp.json().get("email")
        if not email:
            raise OAuthError("Google did not return an email address for this account.")
        return ProfileInfo(email=email)

    # --- Gmail API ---------------------------------------------------------

    async def send_message(
        self,
        access_token: str,
        *,
        from_email: str,
        from_name: str,
        to_email: str,
        subject: str,
        body: str,
        message_id: str,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> str:
        """Send via the Gmail API. Returns Google's thread id."""
        mime = MimeMessage()
        mime["To"] = to_email
        mime["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        mime["Subject"] = subject
        mime["Message-ID"] = message_id
        if in_reply_to:
            mime["In-Reply-To"] = in_reply_to
        if references:
            mime["References"] = " ".join(references)
        mime.set_content(body)

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{GMAIL_API}/messages/send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw},
            )
        if resp.status_code not in (200, 201):
            raise OAuthError(_google_error(resp, "Gmail refused to send the message"))
        return resp.json().get("threadId", "")


def _token_set(data: dict) -> TokenSet:
    expires_in = int(data.get("expires_in", 3600))
    return TokenSet(
        access_token=data["access_token"],
        # Deliberately pessimistic by a minute, so a token is never used in the moment
        # it lapses in flight.
        expires_at=datetime.now(UTC) + timedelta(seconds=max(60, expires_in - 60)),
        refresh_token=data.get("refresh_token"),
        scopes=tuple(data.get("scope", "").split()),
    )


def _is_json(resp: httpx.Response) -> bool:
    return resp.headers.get("content-type", "").startswith("application/json")


def _google_error(resp: httpx.Response, prefix: str) -> str:
    """Turn Google's error body into something worth showing a user."""
    detail = ""
    if _is_json(resp):
        body = resp.json()
        detail = body.get("error_description") or body.get("error") or ""
        if isinstance(body.get("error"), dict):
            detail = body["error"].get("message", "")
    return f"{prefix} (Google returned {resp.status_code}{f': {detail}' if detail else ''})."
