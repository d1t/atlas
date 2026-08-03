"""The mailbox provider abstraction.

Strategy execution asks for "a mailbox for this user" and gets something that can send
and fetch. It does not know whether that is OAuth, an App Password, or nothing at all —
which is the point: swapping the transport must not touch execution logic.

Two implementations:

* :class:`~app.integrations.gmail_oauth.GmailOAuthProvider` — per-user OAuth, the
  customer-facing path.
* :class:`~app.integrations.gmail.GmailClient` — one shared mailbox over SMTP/IMAP, a
  development and administrator fallback. It cannot serve multiple customers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.integrations.gmail import FetchedEmail, SendResult


@dataclass(frozen=True)
class ProviderStatus:
    """What the UI needs to render the integration panel honestly."""

    provider: str
    connected: bool
    #: "live" | "offline" | "needs_reconnect" | "unavailable"
    mode: str
    address: str = ""
    detail: str = ""
    fault: str | None = None
    missing_scopes: tuple[str, ...] = ()
    connected_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def can_send(self) -> bool:
        return self.connected and self.mode == "live"


@runtime_checkable
class EmailProvider(Protocol):
    """Everything execution is allowed to assume about a mailbox."""

    @property
    def configured(self) -> bool:
        """Whether a real send would leave the building."""

    @property
    def address(self) -> str:
        """The address mail is sent from."""

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        *,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
    ) -> SendResult: ...

    async def fetch_replies(
        self, *, since: datetime | None = None, limit: int = 50
    ) -> list[FetchedEmail]: ...
