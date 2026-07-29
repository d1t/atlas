"""Tests for the approval policy.

Almost every test here is a non-happy path, because the happy path (a message goes out
after a human clicks approve) is not where the danger is. The danger is a message going
out that nobody looked at.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.email import EmailMessage
from app.models.execution import PreAuthorizationGrant
from app.services import approval_policy
from app.services.approval_policy import EmailContext, body_fingerprint
from app.services.execution_service import requires_approval_for

KNOWN = "buyer@dangote.example.com"
STRANGER = "someone@newcompany.example.com"


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'policy.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        # A prior message makes KNOWN an established contact.
        s.add(
            EmailMessage(
                direction="outbound",
                to_email=KNOWN,
                subject="Intro",
                body="Hello",
                status="sent",
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


def _ctx(**kw) -> EmailContext:
    base = {
        "recipient": KNOWN,
        "subject": "Following up",
        "body": "Just checking in on our conversation. Any thoughts?",
        "thread_key": "thread-1",
        "template_key": "followup_v1",
    }
    base.update(kw)
    return EmailContext(**base)


async def _grant(session, **kw) -> PreAuthorizationGrant:
    defaults = {
        "strategy_id": 1,
        "action_type": "send_email",
        "thread_key": "thread-1",
        "recipient": KNOWN,
        "template_key": "followup_v1",
        "max_messages": 3,
        "used_count": 0,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
    }
    defaults.update(kw)
    grant = PreAuthorizationGrant(**defaults)
    session.add(grant)
    await session.commit()
    return grant


# --- Defaults --------------------------------------------------------------


async def test_send_email_requires_approval_by_default(session):
    """With no grant in place, nothing gets sent unattended."""
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx()
    )
    assert decision.requires_approval
    assert "no standing authorisation" in decision.reason


def test_type_level_gate_is_conservative_for_email():
    """Callers that can't inspect the draft must still get a gate."""
    assert requires_approval_for("send_email") is True
    assert requires_approval_for("send_email", trusted=True) is True


def test_irreversible_actions_can_never_be_pre_authorised():
    for action_type in (
        "accept_terms",
        "commit_funds",
        "sign_document",
        "delete_data",
        "share_confidential",
        "change_strategy_target",
    ):
        assert requires_approval_for(action_type, trusted=True) is True


async def test_unknown_action_type_is_gated(session):
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="wire_transfer", email=None
    )
    assert decision.requires_approval
    assert decision.triggers == ("unknown_action_type",)


async def test_internal_actions_run_unattended(session):
    for action_type in ("research", "draft_email", "analyse_replies", "flag_risk"):
        decision = await approval_policy.evaluate(
            session, strategy_id=1, action_type=action_type
        )
        assert not decision.requires_approval


async def test_email_that_cannot_be_inspected_is_gated(session):
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=None
    )
    assert decision.requires_approval
    assert decision.triggers == ("uninspectable",)


# --- Conditions no grant can override --------------------------------------


async def test_grant_does_not_cover_first_contact(session):
    await _grant(session, recipient=STRANGER)
    decision = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(recipient=STRANGER),
    )
    assert decision.requires_approval
    assert "first_contact" in decision.triggers


@pytest.mark.parametrize(
    "body",
    [
        "Happy to confirm USD 430 per MT CFR Lagos.",
        "Our offer is $430/MT, payment by letter of credit at sight.",
        "We hereby accept the terms set out in your SPA.",
        "Please find the price indication attached; incoterm CFR.",
        "We can commit to 50,000 MT for Q3.",
    ],
)
async def test_grant_does_not_cover_commercial_language(session, body):
    await _grant(session)
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx(body=body)
    )
    assert decision.requires_approval, f"should have been gated: {body}"
    assert "commercial_language" in decision.triggers


async def test_grant_does_not_cover_attachments(session):
    await _grant(session)
    decision = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(has_attachments=True),
    )
    assert decision.requires_approval
    assert "attachments" in decision.triggers


async def test_grant_does_not_cover_a_materially_changed_draft(session):
    await _grant(session)
    decision = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(materially_changed=True),
    )
    assert decision.requires_approval
    assert "materially_changed" in decision.triggers


# --- Grant scope -----------------------------------------------------------


async def test_constrained_followup_is_allowed_under_a_grant(session):
    """The case the whole mechanism exists for: a plain chase-up on an approved thread."""
    grant = await _grant(session)
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx()
    )
    assert not decision.requires_approval
    assert decision.grant_id == grant.id
    assert decision.risk == "low"
    assert "1/3 messages" in decision.reason


async def test_paused_grant_stops_sending_immediately(session):
    await _grant(session, paused=True)
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx()
    )
    assert decision.requires_approval
    assert "paused" in decision.reason


async def test_revoked_grant_does_not_apply(session):
    await _grant(session, revoked_at=datetime.now(UTC))
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx()
    )
    assert decision.requires_approval


async def test_expired_grant_does_not_apply(session):
    await _grant(session, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx()
    )
    assert decision.requires_approval
    assert "expired" in decision.reason


async def test_message_cap_binds(session):
    grant = await _grant(session, max_messages=2)
    for _ in range(2):
        decision = await approval_policy.evaluate(
            session, strategy_id=1, action_type="send_email", email=_ctx()
        )
        assert not decision.requires_approval
        await approval_policy.consume_grant(session, grant.id)
        await session.commit()

    decision = await approval_policy.evaluate(
        session, strategy_id=1, action_type="send_email", email=_ctx()
    )
    assert decision.requires_approval
    assert "exhausted" in decision.reason


async def test_grant_does_not_leak_to_another_thread(session):
    await _grant(session, thread_key="thread-1")
    decision = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(thread_key="thread-2"),
    )
    assert decision.requires_approval


async def test_grant_does_not_leak_to_another_strategy(session):
    await _grant(session, strategy_id=1)
    decision = await approval_policy.evaluate(
        session, strategy_id=2, action_type="send_email", email=_ctx()
    )
    assert decision.requires_approval


async def test_grant_does_not_cover_a_different_template(session):
    await _grant(session, template_key="followup_v1")
    decision = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(template_key="hard_close_v1"),
    )
    assert decision.requires_approval
    assert "template" in decision.reason


async def test_grant_bound_to_wording_rejects_rewritten_body(session):
    approved = "Just checking in on our conversation. Any thoughts?"
    await _grant(session, approved_body_hash=body_fingerprint(approved))

    # Reflowed whitespace and casing is the same message.
    reflowed = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(body="Just checking in on our  conversation.\nAny thoughts?"),
    )
    assert not reflowed.requires_approval

    rewritten = await approval_policy.evaluate(
        session,
        strategy_id=1,
        action_type="send_email",
        email=_ctx(body="Actually, we need an answer today or we walk away."),
    )
    assert rewritten.requires_approval
    assert "approved wording" in rewritten.reason


def test_conversational_text_is_not_flagged_as_commercial():
    """Guards the detector against being so broad it gates everything."""
    assert approval_policy.commercial_terms_in("Thanks, that's helpful.") == ()
    assert approval_policy.commercial_terms_in("Are you free on Thursday?") == ()
    # ...but anything money-shaped is caught.
    assert approval_policy.commercial_terms_in("We can do $430/MT.")
