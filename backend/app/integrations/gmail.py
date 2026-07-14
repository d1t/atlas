"""Gmail integration via SMTP (send) + IMAP (read replies), authenticated with
a Gmail App Password.

Design notes
------------
* Credentials live in :class:`app.core.config.Settings` (env / secrets), never
  in the database — an ``EmailMessage`` row only records *what* was sent, not
  the password used to send it.
* When no credentials are configured the client runs in **OFFLINE mode**:
  :meth:`GmailClient.send` records the message and returns a synthetic
  ``message_id`` (prefixed ``offline-``) *without* transmitting anything, and
  :meth:`GmailClient.fetch_replies` returns an empty list. This lets the whole
  send/log/sync flow be exercised in tests and local dev without a real inbox.
* ``smtplib`` / ``imaplib`` are blocking, so the public methods are ``async``
  and offload the blocking work to a worker thread via ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage as PyEmailMessage
from email.utils import formataddr, parsedate_to_datetime

from app.core.config import Settings, get_settings

logger = logging.getLogger("atlas.gmail")


@dataclass
class SendResult:
    """Outcome of a send attempt."""

    message_id: str
    status: str  # "sent" | "offline" | "failed"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("sent", "offline")


@dataclass
class FetchedEmail:
    """A single inbound message parsed from the IMAP inbox."""

    message_id: str
    from_email: str
    from_name: str
    subject: str
    body: str
    received_at: datetime | None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_email(raw: str) -> str:
    m = _EMAIL_RE.search(raw or "")
    return m.group(0).lower() if m else ""


def _body_from_message(msg: email.message.Message) -> str:
    """Prefer the text/plain part; fall back to a crude strip of text/html."""
    plain: str | None = None
    html: str | None = None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        if payload is not None:
            try:
                plain = payload.decode(charset, errors="replace")
            except LookupError:
                plain = payload.decode("utf-8", errors="replace")
    if plain is not None:
        return plain.strip()
    if html is not None:
        return re.sub(r"<[^>]+>", " ", html).strip()
    return ""


class GmailClient:
    """Thin async wrapper over smtplib/imaplib for a single Gmail account."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def configured(self) -> bool:
        return self.settings.gmail_configured

    @property
    def address(self) -> str:
        return self.settings.gmail_address

    # --- send -------------------------------------------------------------

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
        if not self.configured:
            logger.info("Gmail OFFLINE — not sending to %s (subject=%r)", to_email, subject)
            return SendResult(message_id=f"offline-{message_id}", status="offline")
        try:
            return await asyncio.to_thread(
                self._send_blocking,
                to_email,
                subject,
                body,
                message_id,
                in_reply_to,
                references or [],
            )
        except Exception as exc:  # pragma: no cover - network failure path
            logger.exception("Gmail send failed")
            return SendResult(message_id=message_id, status="failed", error=str(exc))

    def _send_blocking(
        self,
        to_email: str,
        subject: str,
        body: str,
        message_id: str,
        in_reply_to: str | None,
        references: list[str],
    ) -> SendResult:
        s = self.settings
        msg = PyEmailMessage()
        from_name = s.gmail_from_name or ""
        msg["From"] = formataddr((from_name, s.gmail_address)) if from_name else s.gmail_address
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            refs = list(references)
            if in_reply_to not in refs:
                refs.append(in_reply_to)
            if refs:
                msg["References"] = " ".join(refs)
        elif references:
            msg["References"] = " ".join(references)
        msg.set_content(body)

        context = ssl.create_default_context()
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(s.gmail_address, s.gmail_app_password)
            server.send_message(msg)
        logger.info("Gmail sent to %s (subject=%r)", to_email, subject)
        return SendResult(message_id=message_id, status="sent")

    # --- read -------------------------------------------------------------

    async def fetch_replies(
        self, *, since: datetime | None = None, limit: int = 50
    ) -> list[FetchedEmail]:
        if not self.configured:
            logger.info("Gmail OFFLINE — skipping reply sync")
            return []
        try:
            return await asyncio.to_thread(self._fetch_blocking, since, limit)
        except Exception:  # pragma: no cover - network failure path
            logger.exception("Gmail reply sync failed")
            return []

    def _fetch_blocking(
        self, since: datetime | None, limit: int
    ) -> list[FetchedEmail]:
        s = self.settings
        if since is None:
            since = datetime.now(UTC) - timedelta(days=s.imap_lookback_days)
        results: list[FetchedEmail] = []
        imap = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
        try:
            imap.login(s.gmail_address, s.gmail_app_password)
            imap.select("INBOX")
            criterion = since.strftime("%d-%b-%Y")
            typ, data = imap.search(None, "SINCE", criterion)
            if typ != "OK" or not data or not data[0]:
                return results
            ids = data[0].split()
            for num in reversed(ids[-limit:]):
                typ, msg_data = imap.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                msg = email.message_from_bytes(raw)
                from_raw = _decode(msg.get("From"))
                received_at: datetime | None
                try:
                    received_at = parsedate_to_datetime(msg.get("Date"))
                except Exception:
                    received_at = None
                refs_raw = _decode(msg.get("References"))
                results.append(
                    FetchedEmail(
                        message_id=(msg.get("Message-ID") or "").strip(),
                        from_email=_extract_email(from_raw),
                        from_name=re.sub(r"<[^>]+>", "", from_raw).strip().strip('"'),
                        subject=_decode(msg.get("Subject")),
                        body=_body_from_message(msg),
                        received_at=received_at,
                        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
                        references=refs_raw.split() if refs_raw else [],
                    )
                )
        finally:
            try:
                imap.logout()
            except Exception:
                pass
        return results


def get_gmail_client() -> GmailClient:
    return GmailClient()
