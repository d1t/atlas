"""Tests for agent capability execution.

Planning is safe by construction — it cannot reach the outside world. Execution can, so
the tests that matter here are the ones asserting an agent is *stopped*: gated before
sending, blocked when an input is missing, refused when it would repeat outreach, and
unable to declare a task done on the strength of having tried.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import executor
from app.models import Base
from app.models.email import EmailMessage
from app.models.execution import (
    AgentAction,
    Approval,
    Evidence,
    PreAuthorizationGrant,
)
from app.models.opportunity import Opportunity, SupplierLead
from app.models.strategy import Strategy, StrategyTask
from app.models.user import User
from app.services import execution_service

NOW = datetime(2026, 7, 13, tzinfo=UTC)


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'exec.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def strategy(session) -> Strategy:
    user = User(email="trader@atlas.example.com", hashed_password="x")
    session.add(user)
    s = Strategy(
        title="Control the Brazil to Nigeria sugar chain",
        commodity="sugar",
        origin_region="Brazil",
        destination_region="Nigeria",
        target_volume_mt=50000,
        horizon="quarter",
        pillars={},
    )
    session.add(s)
    await session.flush()
    return s


async def _opportunity(session, strategy) -> Opportunity:
    opp = Opportunity(
        title="Brazil sugar to Nigeria",
        commodity="sugar",
        destination_country="Nigeria",
        volume_mt=25000,
        status="active",
    )
    session.add(opp)
    await session.flush()
    return opp


async def _task(session, strategy, **kw) -> StrategyTask:
    defaults = {
        "strategy_id": strategy.id,
        "pillar": "supply",
        "kind": "task",
        "title": "Send RFQ to Atlas Mill Ltda",
        "status": "todo",
        "priority": "high",
        "position": 0,
        "assignee": "agent",
        "agent_key": "supply",
        "capability": "send_email",
        "source": "agent",
    }
    defaults.update(kw)
    task = StrategyTask(**defaults)
    session.add(task)
    await session.flush()
    return task


async def _supplier_lead(session, opp, **kw) -> SupplierLead:
    defaults = {
        "opportunity_id": opp.id,
        "supplier_name": "Atlas Mill Ltda",
        "email": "sales@atlasmill.example.com",
        "status": "new",
    }
    defaults.update(kw)
    lead = SupplierLead(**defaults)
    session.add(lead)
    await session.flush()
    return lead


async def _user(session) -> User:
    return (await session.execute(select(User))).scalars().first()


# --- Sending is gated ---------------------------------------------------------


async def test_first_outreach_is_prepared_but_not_sent(session, strategy):
    """The agent must do the work and then stop, not ask permission to start."""
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp)
    await _task(session, strategy, opportunity_id=opp.id, supplier_lead_id=lead.id)

    report = await executor.execute(session, strategy)

    assert [o.state for o in report.outcomes] == ["awaiting_approval"]
    action = (await session.execute(select(AgentAction))).scalars().one()
    assert action.state == "awaiting_approval"
    # The reviewer must see the actual message, not a promise of one.
    assert action.payload["to_email"] == "sales@atlasmill.example.com"
    assert action.payload["subject"]
    assert action.payload["body"]
    assert action.email_message_id is None

    approval = (await session.execute(select(Approval))).scalars().one()
    assert approval.status == "pending"


async def test_approving_sends_and_waits_rather_than_completing(session, strategy):
    """A delivered email proves an attempt, not an outcome."""
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp)
    task = await _task(
        session,
        strategy,
        opportunity_id=opp.id,
        supplier_lead_id=lead.id,
        requires_evidence=True,
        acceptance_criteria="A written quotation from the supplier is on file.",
    )
    await executor.execute(session, strategy)

    approval = (await session.execute(select(Approval))).scalars().one()
    await execution_service.decide_approval(
        session, approval, approved=True, user=await _user(session)
    )
    action = await session.get(AgentAction, approval.action_id)
    await executor.run_action(session, action)

    assert action.state == "waiting_response"
    assert action.email_message_id is not None
    await session.refresh(task)
    assert task.status == "in_progress"
    assert task.completed_at is None


async def test_a_second_pass_does_not_contact_the_supplier_again(session, strategy):
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp)
    await _task(session, strategy, opportunity_id=opp.id, supplier_lead_id=lead.id)

    await executor.execute(session, strategy)
    second = await executor.execute(session, strategy)

    assert second.outcomes == []
    assert len((await session.execute(select(AgentAction))).scalars().all()) == 1


async def _grant(session, strategy, task, **kw) -> PreAuthorizationGrant:
    defaults = {
        "strategy_id": strategy.id,
        "action_type": "send_email",
        "thread_key": f"task:{task.id}",
        "recipient": "sales@atlasmill.example.com",
        "template_key": "supply:send_email",
        "max_messages": 3,
        "expires_at": datetime.now(UTC) + timedelta(days=7),
        "created_by_id": (await _user(session)).id,
    }
    defaults.update(kw)
    grant = PreAuthorizationGrant(**defaults)
    session.add(grant)
    await session.flush()
    return grant


async def test_an_rfq_is_gated_even_on_a_granted_thread(session, strategy):
    """A grant covers routine chasing. An RFQ asks for a price, so it never qualifies."""
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp, status="contacted")
    task = await _task(session, strategy, opportunity_id=opp.id, supplier_lead_id=lead.id)
    grant = await _grant(session, strategy, task)

    report = await executor.execute(session, strategy)

    assert [o.state for o in report.outcomes] == ["awaiting_approval"]
    action = (await session.execute(select(AgentAction))).scalars().one()
    assert action.requires_approval
    assert action.payload["grant_id"] is None
    # The reason has to name what tripped, or the user cannot fix it.
    assert "pricing or contractual" in action.payload["policy"].lower()
    await session.refresh(grant)
    assert grant.used_count == 0


async def test_a_plain_follow_up_on_a_granted_thread_goes_without_asking(
    session, strategy, monkeypatch
):
    """Pre-authorisation has to actually work, or the permission model is theatre."""
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp, status="contacted")
    task = await _task(session, strategy, opportunity_id=opp.id, supplier_lead_id=lead.id)
    grant = await _grant(session, strategy, task)
    # A grant only applies to someone already spoken to; first contact is never covered.
    session.add(
        EmailMessage(
            direction="outbound",
            status="sent",
            to_email="sales@atlasmill.example.com",
            from_email="desk@atlas.example.com",
            subject="Initial approved enquiry",
            body="Approved by a human earlier.",
        )
    )
    await session.flush()

    async def _plain(*a, **kw):
        return {
            "to_email": "sales@atlasmill.example.com",
            "to_name": "Atlas Mill Ltda",
            "subject": "Following up on our enquiry",
            "body": (
                "Dear Atlas Mill,\n\nJust checking whether you have had a chance to "
                "look at our enquiry. Happy to answer any questions.\n\nBest regards,"
            ),
            "reason": None,
        }

    monkeypatch.setattr("app.services.strategy_service.draft_task_email", _plain)

    report = await executor.execute(session, strategy)

    assert [o.state for o in report.outcomes] == ["waiting_response"]
    action = (await session.execute(select(AgentAction))).scalars().one()
    assert action.requires_approval is False
    assert action.email_message_id is not None
    await session.refresh(grant)
    assert grant.used_count == 1
    # Auto-sending is never silent.
    assert action.payload["policy"]


async def test_a_grant_is_only_spent_on_a_message_that_went_out(session, strategy):
    """A gated action must not burn the user's allowance."""
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp)
    task = await _task(session, strategy, opportunity_id=opp.id, supplier_lead_id=lead.id)
    grant = await _grant(
        session,
        strategy,
        task,
        thread_key="unrelated-thread",
        recipient="someone.else@example.com",
    )

    await executor.execute(session, strategy)

    await session.refresh(grant)
    assert grant.used_count == 0
    assert task.status == "todo"


# --- Missing inputs block rather than fail silently ---------------------------


async def test_a_lead_with_no_email_blocks_with_a_reason(session, strategy):
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp, email=None)
    task = await _task(session, strategy, opportunity_id=opp.id, supplier_lead_id=lead.id)

    report = await executor.execute(session, strategy)

    assert [o.state for o in report.outcomes] == ["blocked"]
    # Nothing was proposed, because there is nothing that could be sent.
    assert (await session.execute(select(AgentAction))).scalars().all() == []
    await session.refresh(task)
    assert task.status == "todo"
    assert "no email" in (task.blocked_reason or "").lower()


async def test_supplier_research_with_no_results_blocks_the_task(
    session, strategy, monkeypatch
):
    """An empty search is a real answer, and must not read as success."""
    opp = await _opportunity(session, strategy)
    task = await _task(
        session,
        strategy,
        title="Discover and qualify additional suppliers",
        capability="research_suppliers",
        opportunity_id=opp.id,
        requires_evidence=True,
        acceptance_criteria="At least three credible suppliers are shortlisted.",
    )

    async def _nothing(*a, **kw):
        return {"task_id": task.id, "commodity": "sugar", "candidates": []}

    monkeypatch.setattr(
        "app.services.strategy_service.source_suppliers_for_task", _nothing
    )

    report = await executor.execute(session, strategy)

    assert [o.state for o in report.outcomes] == ["blocked"]
    await session.refresh(task)
    assert task.status != "done"
    assert "no credible candidates" in (task.blocked_reason or "")


async def test_supplier_research_records_the_shortlist_as_evidence(
    session, strategy, monkeypatch
):
    opp = await _opportunity(session, strategy)
    task = await _task(
        session,
        strategy,
        title="Discover and qualify additional suppliers",
        capability="research_suppliers",
        opportunity_id=opp.id,
        requires_evidence=True,
        acceptance_criteria="At least one credible supplier is shortlisted.",
    )

    async def _found(*a, **kw):
        return {
            "task_id": task.id,
            "commodity": "sugar",
            "candidates": [
                {
                    "name": "Copersucar",
                    "country": "Brazil",
                    "website": "https://copersucar.example.com",
                    "email": "trade@copersucar.example.com",
                    "credibility_score": 82,
                    "risk_score": 12,
                    "red_flags": [],
                }
            ],
        }

    monkeypatch.setattr(
        "app.services.strategy_service.source_suppliers_for_task", _found
    )

    report = await executor.execute(session, strategy)

    assert [o.state for o in report.outcomes] == ["completed"]
    evidence = (await session.execute(select(Evidence))).scalars().all()
    assert len(evidence) == 1
    assert "Copersucar" in evidence[0].description
    assert evidence[0].created_by_type == "agent"
    await session.refresh(task)
    assert task.status == "done"
    # Completed by an agent, so nobody has verified it.
    assert task.verified_by_id is None


# --- An agent cannot talk its way past the gates ------------------------------


async def test_an_agent_cannot_override_the_evidence_gate(session, strategy):
    task = await _task(
        session,
        strategy,
        title="Secure off-take for the remaining 30,000 MT",
        capability=None,
        requires_evidence=True,
        acceptance_criteria="A countersigned off-take contract is on file.",
    )
    with pytest.raises(execution_service.EvidenceRequired):
        await execution_service.complete_task(
            session,
            task,
            actor_type="agent",
            override_reason="I am confident this is done.",
        )
    assert task.status == "todo"


async def test_a_task_waiting_on_a_prerequisite_is_not_executed(session, strategy):
    opp = await _opportunity(session, strategy)
    lead = await _supplier_lead(session, opp)
    blocker = await _task(
        session,
        strategy,
        title="Discover and qualify additional suppliers",
        capability="research_suppliers",
    )
    dependent = await _task(
        session,
        strategy,
        position=1,
        opportunity_id=opp.id,
        supplier_lead_id=lead.id,
    )
    dependent.depends_on_ids = [blocker.id]
    await session.flush()

    ready = await executor._ready_tasks(session, strategy, limit=10)

    assert dependent.id not in [t.id for t in ready]


async def test_human_only_tasks_are_left_alone(session, strategy):
    await _task(session, strategy, assignee="human", capability=None)

    report = await executor.execute(session, strategy)

    assert report.outcomes == []
    assert (await session.execute(select(AgentAction))).scalars().all() == []
