"""Decides whether an agent action needs a human before it happens.

The rule is not "which action type is this" but "what is this specific action about to
do". Two `send_email` actions can warrant completely different treatment: the fourth
chase-up on a thread the user already approved is not the same event as a first
approach to a stranger quoting a price.

Everything here fails closed. Any condition we cannot positively verify — an unparseable
draft, a missing grant, an unrecognised action type — results in approval being
required. The cost of a needless approval is an interruption; the cost of a wrong send
is a commercial commitment made in the user's name.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import EmailMessage
from app.models.execution import (
    ALWAYS_APPROVED_ACTION_TYPES,
    AUTONOMOUS_ACTION_TYPES,
    PRE_AUTHORISABLE_ACTION_TYPES,
    PreAuthorizationGrant,
)

#: Wording that signals the message carries commercial weight. Matching is deliberately
#: broad: a false positive costs one approval click, a false negative sends a price.
#: Word-boundary anchored so "sconto" or "priceless" don't trip it.
_COMMERCIAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:usd|eur|gbp|ngn|brl)\s*[\d,]+",
        r"[$£€]\s*[\d,]+",
        r"\b[\d,]+(?:\.\d+)?\s*(?:usd|eur|gbp)\b",
        r"\bper\s+(?:mt|tonne|ton|metric\s+ton)\b",
        r"\b(?:price|pricing|quote|quotation|offer|bid|premium|discount)\b",
        r"\b(?:incoterm|cif|cfr|fob|exw|ddp)\b",
        r"\b(?:letter\s+of\s+credit|lc\s+at\s+sight|\bl/c\b)\b",
        r"\b(?:contract|agreement|spa|mou|loi|term\s+sheet|binding)\b",
        r"\b(?:we\s+(?:agree|accept|confirm)|hereby\s+(?:agree|accept))\b",
        r"\b(?:payment\s+terms|deposit|prepayment|advance\s+payment)\b",
        r"\b(?:commit|undertake|guarantee|warrant)\b",
    )
)


@dataclass(frozen=True)
class EmailContext:
    """What an agent is about to send, as far as the policy needs to know."""

    recipient: str
    body: str
    subject: str = ""
    thread_key: str = ""
    template_key: str = ""
    has_attachments: bool = False
    #: True when the agent changed a draft a human already signed off.
    materially_changed: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    requires_approval: bool
    #: Why, in words that can go straight into the approval queue or the audit log.
    reason: str
    risk: str = "medium"
    grant_id: int | None = None
    #: Every rule that fired, so the UI can explain a decision rather than assert it.
    triggers: tuple[str, ...] = field(default_factory=tuple)


def body_fingerprint(body: str) -> str:
    """Hash a draft ignoring whitespace and case, so trivial reflow isn't a change."""
    normalised = re.sub(r"\s+", " ", body).strip().lower()
    return hashlib.sha256(normalised.encode()).hexdigest()


def commercial_terms_in(text: str) -> tuple[str, ...]:
    """Return the commercial signals found, empty if the text is purely conversational."""
    return tuple(
        sorted({m.pattern for m in _COMMERCIAL_PATTERNS if m.search(text)})
    )


async def _is_known_contact(
    db: AsyncSession, *, strategy_id: int, recipient: str
) -> bool:
    """True when this address has already been corresponded with, either direction.

    First contact with a new counterparty always deserves a human, so this checks the
    message history rather than trusting the agent's own claim about the thread.
    """
    address = recipient.strip().lower()
    if not address:
        return False
    count = await db.scalar(
        select(func.count())
        .select_from(EmailMessage)
        .where(
            (func.lower(EmailMessage.to_email) == address)
            | (func.lower(EmailMessage.from_email) == address)
        )
    )
    return bool(count)


async def _usable_grant(
    db: AsyncSession, *, strategy_id: int, action_type: str, ctx: EmailContext
) -> tuple[PreAuthorizationGrant | None, str]:
    """Find a live grant covering this exact message, or explain why none applies."""
    now = datetime.now(UTC)
    grants = (
        await db.execute(
            select(PreAuthorizationGrant).where(
                PreAuthorizationGrant.strategy_id == strategy_id,
                PreAuthorizationGrant.action_type == action_type,
                PreAuthorizationGrant.thread_key == ctx.thread_key,
            )
        )
    ).scalars().all()

    if not grants:
        return None, "no standing authorisation covers this thread"

    for grant in grants:
        if grant.revoked_at is not None:
            continue
        if grant.paused:
            return None, "standing authorisation is paused"
        expires = grant.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= now:
            return None, "standing authorisation has expired"
        if grant.used_count >= grant.max_messages:
            return None, (
                f"standing authorisation exhausted "
                f"({grant.used_count}/{grant.max_messages} messages used)"
            )
        if grant.recipient.strip().lower() != ctx.recipient.strip().lower():
            continue
        if grant.template_key != ctx.template_key:
            return None, "draft does not use the authorised template"
        if (
            grant.approved_body_hash is not None
            and grant.approved_body_hash != body_fingerprint(ctx.body)
        ):
            return None, "draft differs from the approved wording"
        return grant, ""

    return None, "no standing authorisation covers this recipient"


async def evaluate(
    db: AsyncSession,
    *,
    strategy_id: int,
    action_type: str,
    email: EmailContext | None = None,
) -> PolicyDecision:
    """Decide whether ``action_type`` may proceed unattended."""
    if action_type in ALWAYS_APPROVED_ACTION_TYPES:
        return PolicyDecision(
            True,
            "This action type always requires explicit approval.",
            risk="high",
            triggers=("always_gated",),
        )

    if action_type in AUTONOMOUS_ACTION_TYPES:
        return PolicyDecision(
            False, "Internal action with no external effect.", risk="low"
        )

    if action_type not in PRE_AUTHORISABLE_ACTION_TYPES:
        # Unknown capability: gated by default. Gaining an ability is not the same as
        # gaining permission to use it unsupervised.
        return PolicyDecision(
            True,
            f"'{action_type}' is not a recognised action type, so it requires approval.",
            risk="high",
            triggers=("unknown_action_type",),
        )

    if email is None:
        return PolicyDecision(
            True,
            "Outbound message could not be inspected, so it requires approval.",
            risk="high",
            triggers=("uninspectable",),
        )

    # Conditions that no standing authorisation can cover.
    triggers: list[str] = []
    if ctx_terms := commercial_terms_in(f"{email.subject}\n{email.body}"):
        triggers.append("commercial_language")
    if email.has_attachments:
        triggers.append("attachments")
    if email.materially_changed:
        triggers.append("materially_changed")
    if not await _is_known_contact(
        db, strategy_id=strategy_id, recipient=email.recipient
    ):
        triggers.append("first_contact")

    if triggers:
        explanations = {
            "commercial_language": (
                "contains pricing or contractual language"
                + (
                    f" ({len(ctx_terms)} signal{'s' if len(ctx_terms) > 1 else ''})"
                    if ctx_terms
                    else ""
                )
            ),
            "attachments": "has attachments",
            "materially_changed": "differs materially from the approved draft",
            "first_contact": "is a first approach to a new contact",
        }
        reason = "Requires approval: this message " + ", ".join(
            explanations[t] for t in triggers
        )
        return PolicyDecision(
            True, reason + ".", risk="high", triggers=tuple(triggers)
        )

    grant, why_not = await _usable_grant(
        db, strategy_id=strategy_id, action_type=action_type, ctx=email
    )
    if grant is None:
        return PolicyDecision(
            True,
            f"Requires approval: {why_not}.",
            risk="medium",
            triggers=("no_grant",),
        )

    return PolicyDecision(
        False,
        (
            f"Covered by standing authorisation #{grant.id} "
            f"({grant.used_count + 1}/{grant.max_messages} messages, "
            f"expires {grant.expires_at:%Y-%m-%d})."
        ),
        risk="low",
        grant_id=grant.id,
    )


async def consume_grant(db: AsyncSession, grant_id: int) -> None:
    """Count a message against its grant so the cap actually binds."""
    grant = await db.get(PreAuthorizationGrant, grant_id)
    if grant is not None:
        grant.used_count += 1
