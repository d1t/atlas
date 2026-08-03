"""Tests for the orchestrator and pillar agents.

The central claim being tested is that agents plan from real pipeline figures. So the
most important tests here are the ones asserting an agent produces *nothing*: a planner
that always emits plausible work is indistinguishable from one that understands the
business until you act on its output.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import orchestrator
from app.agents.base import AgentContext, LeadView, TaskSpec
from app.agents.pillars import (
    DemandAgent,
    ExecutionAgent,
    OriginationAgent,
    SupplyAgent,
)
from app.models import Base
from app.models.deal import Deal
from app.models.email import EmailMessage
from app.models.execution import AgentAction, Evidence
from app.models.opportunity import BuyerLead, Opportunity, SupplierLead
from app.models.strategy import Strategy, StrategyTask

NOW = datetime(2026, 7, 13, tzinfo=UTC)


@pytest.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agents.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _strategy(**kw) -> Strategy:
    defaults = {
        "id": 1,
        "title": "Control the Brazil to Nigeria sugar chain",
        "commodity": "sugar",
        "origin_region": "Brazil",
        "destination_region": "Nigeria",
        "target_volume_mt": 50000,
        "target_margin_per_mt": 18,
        "horizon": "quarter",
        "pillars": {},
    }
    defaults.update(kw)
    return Strategy(**defaults)


def _ctx(**kw) -> AgentContext:
    kw.setdefault("strategy", _strategy())
    kw.setdefault("now", NOW)
    return AgentContext(**kw)


def _buyer(**kw) -> LeadView:
    defaults = {
        "id": 1,
        "name": "Dangote Sugar",
        "email": "buyer@dangote.example.com",
        "status": "new",
        "opportunity_id": 1,
    }
    defaults.update(kw)
    return LeadView(**defaults)


def _supplier(**kw) -> LeadView:
    defaults = {
        "id": 1,
        "name": "Atlas Mill Ltda",
        "email": "sales@atlasmill.example.com",
        "status": "new",
        "opportunity_id": 1,
    }
    defaults.update(kw)
    return LeadView(**defaults)


def _flatten(specs: list[TaskSpec]) -> list[TaskSpec]:
    out: list[TaskSpec] = []
    for s in specs:
        out.append(s)
        out.extend(_flatten(s.children))
    return out


# --- Agents must produce nothing when there is no gap -----------------------


def test_demand_agent_plans_nothing_when_target_is_committed():
    ctx = _ctx(
        buyer_leads=(_buyer(status="committed", volume_mt=50000),),
    )
    assert DemandAgent().decompose(ctx) == []


def test_supply_agent_plans_nothing_when_supply_is_covered():
    ctx = _ctx(
        opportunities=(
            Opportunity(id=1, title="Nigeria sugar", commodity="sugar", volume_mt=50000),
        ),
        supplier_leads=(_supplier(status="shortlisted"),),
    )
    assert SupplyAgent().decompose(ctx) == []


def test_origination_agent_plans_nothing_with_enough_live_opportunities():
    ctx = _ctx(
        opportunities=(
            Opportunity(id=1, title="A", commodity="sugar", status="sourcing"),
            Opportunity(id=2, title="B", commodity="sugar", status="negotiating"),
        )
    )
    assert OriginationAgent().decompose(ctx) == []


def test_execution_agent_ignores_deals_with_nothing_to_progress():
    ctx = _ctx(deals=(Deal(id=1, title="Early deal", commodity="sugar", stage="lead"),))
    assert ExecutionAgent().decompose(ctx) == []


def test_agents_do_not_replan_work_already_on_the_board():
    """Re-running the orchestrator on an unchanged pipeline must not duplicate tasks."""
    ctx = _ctx(buyer_leads=(_buyer(),))
    first = DemandAgent().decompose(ctx)
    assert first

    existing = tuple(
        StrategyTask(
            strategy_id=1,
            pillar=s.pillar,
            title=s.title,
            cadence="weekly",
            priority="medium",
            status="todo",
            source="agent",
        )
        for s in first
    )
    assert DemandAgent().decompose(_ctx(buyer_leads=(_buyer(),), existing_tasks=existing)) == []


# --- Plans are derived from the actual figures ------------------------------


def test_demand_plan_is_sized_from_the_real_uncommitted_volume():
    ctx = _ctx(
        buyer_leads=(
            _buyer(id=1, name="Dangote Sugar", status="committed", volume_mt=20000),
            _buyer(id=2, name="Golden Sugar", status="engaged", volume_mt=15000),
        )
    )
    outcome = DemandAgent().decompose(ctx)[0]
    # 50,000 target - 20,000 committed. The engaged 15,000 does not count.
    assert "30,000 MT" in outcome.title
    assert "20,000 MT of 50,000 MT is committed" in outcome.detail


def test_demand_agent_names_each_real_buyer_rather_than_emitting_filler():
    ctx = _ctx(
        buyer_leads=(
            _buyer(id=1, name="Dangote Sugar", status="new"),
            _buyer(id=2, name="Golden Sugar", status="engaged"),
        )
    )
    titles = [c.title for c in DemandAgent().decompose(ctx)[0].children]
    assert "Open the conversation with Dangote Sugar" in titles
    assert "Convert Golden Sugar from engaged to committed volume" in titles


def test_demand_agent_treats_a_silent_conversation_as_stalled():
    ctx = _ctx(
        buyer_leads=(
            _buyer(status="contacted", last_contacted_at=NOW - timedelta(days=30)),
        )
    )
    titles = [c.title for c in DemandAgent().decompose(ctx)[0].children]
    assert "Revive the stalled Dangote Sugar conversation" in titles


def test_a_lead_with_no_email_is_assigned_to_a_human():
    """An agent must not be given work it has no means of doing."""
    ctx = _ctx(buyer_leads=(_buyer(email=None, status="engaged"),))
    child = DemandAgent().decompose(ctx)[0].children[0]
    assert child.assignee == "human"
    assert child.capability is None
    assert "no email address on file" in child.detail.lower()


def test_no_buyers_at_all_is_a_prospecting_problem_not_a_chasing_one():
    ctx = _ctx(buyer_leads=())
    child = DemandAgent().decompose(ctx)[0].children[0]
    assert child.title == "Build a buyer list for 50,000 MT"


def test_supply_agent_skips_discovery_when_the_funnel_is_already_deep():
    ctx = _ctx(
        supplier_leads=tuple(
            _supplier(id=i, name=f"Mill {i}", status="quoted", price_mt=420)
            for i in range(1, 5)
        )
    )
    titles = [c.title for c in SupplyAgent().decompose(ctx)[0].children]
    assert "Discover and qualify additional suppliers" not in titles
    assert "Verify and shortlist Mill 1" in titles


def test_execution_agent_gates_each_deal_on_its_actual_stage():
    ctx = _ctx(
        deals=(
            Deal(id=1, title="Lagos Q3", commodity="sugar", stage="spa", volume_mt=50000),
        )
    )
    spec = ExecutionAgent().decompose(ctx)[0]
    assert spec.title == "Get the SPA executed — Lagos Q3"
    assert spec.acceptance_criteria == "A countersigned SPA is on file."


# --- Evidence discipline ----------------------------------------------------


def test_every_outcome_is_evidence_gated_with_stated_criteria():
    ctx = _ctx(
        buyer_leads=(_buyer(),),
        supplier_leads=(_supplier(),),
        deals=(Deal(id=1, title="Lagos Q3", commodity="sugar", stage="spa"),),
    )
    specs = _flatten(
        DemandAgent().decompose(ctx)
        + SupplyAgent().decompose(ctx)
        + ExecutionAgent().decompose(ctx)
    )
    for spec in (s for s in specs if s.kind in ("outcome", "milestone")):
        assert spec.requires_evidence, f"{spec.title} is not evidence-gated"
        assert spec.acceptance_criteria, f"{spec.title} states no acceptance criteria"


def test_a_gated_task_cannot_be_declared_without_saying_how_it_is_proven():
    with pytest.raises(ValueError, match="acceptance criteria"):
        TaskSpec(
            title="Secure a buyer",
            pillar="demand",
            requires_evidence=True,
        )


def test_unknown_capabilities_are_rejected_at_declaration():
    with pytest.raises(ValueError, match="Unknown capability"):
        TaskSpec(title="Wire the deposit", pillar="execution", capability="wire_funds")


def test_an_agent_cannot_schedule_a_capability_it_was_not_granted():
    spec = TaskSpec(title="Send it", pillar="demand", capability="send_email")
    with pytest.raises(ValueError, match="not permitted"):
        orchestrator._assert_capabilities(
            "execution_agent", ExecutionAgent.capabilities, spec
        )


# --- Orchestration against the database -------------------------------------


async def _seed(session) -> Strategy:
    strategy = _strategy()
    session.add(strategy)
    session.add(
        Opportunity(
            id=1,
            title="Nigeria sugar 50k MT CFR Lagos",
            commodity="sugar",
            volume_mt=50000,
            status="sourcing",
        )
    )
    await session.flush()
    session.add_all(
        [
            BuyerLead(
                id=1,
                opportunity_id=1,
                buyer_name="Dangote Sugar",
                email="buyer@dangote.example.com",
                status="engaged",
                volume_mt=20000,
            ),
            SupplierLead(
                id=1,
                opportunity_id=1,
                supplier_name="Atlas Mill Ltda",
                email="sales@atlasmill.example.com",
                status="new",
            ),
        ]
    )
    await session.commit()
    return strategy


async def test_orchestrator_persists_a_tree_not_a_flat_list(session):
    strategy = await _seed(session)
    run, created = await orchestrator.plan(session, strategy, now=NOW)
    await session.commit()

    assert run.status == "completed"
    assert created

    roots = [t for t in created if t.parent_id is None]
    children = [t for t in created if t.parent_id is not None]
    assert roots and children, "expected a hierarchy, got a flat list"
    assert {t.kind for t in roots} <= {"outcome"}
    assert all(t.source == "agent" and t.assignee in ("agent", "human") for t in created)


async def test_orchestrator_reasoning_cites_the_figures_it_planned_from(session):
    strategy = await _seed(session)
    run, _ = await orchestrator.plan(session, strategy, now=NOW)
    await session.commit()

    # Nothing is committed yet, so the whole target is still open.
    assert "Target 50,000 MT" in run.reasoning
    assert "gap 50,000 MT" in run.reasoning
    assert "1 buyer lead(s)" in run.reasoning


async def test_rerunning_the_orchestrator_creates_nothing_new(session):
    strategy = await _seed(session)
    _, first = await orchestrator.plan(session, strategy, now=NOW)
    await session.commit()
    assert first

    _, second = await orchestrator.plan(session, strategy, now=NOW)
    await session.commit()
    assert second == []


async def test_sibling_dependencies_are_resolved_to_real_ids(session):
    strategy = _strategy(target_volume_mt=50000)
    session.add(strategy)
    await session.commit()

    _, created = await orchestrator.plan(session, strategy, now=NOW)
    await session.commit()

    dependent = [t for t in created if t.depends_on_ids]
    assert dependent, "expected at least one task to declare a dependency"
    ids = {t.id for t in created}
    for task in dependent:
        assert set(task.depends_on_ids) <= ids


# --- Resuming after a reply -------------------------------------------------


async def _park_action_awaiting_reply(session, strategy) -> tuple[AgentAction, StrategyTask]:
    task = StrategyTask(
        strategy_id=strategy.id,
        pillar="supply",
        title="Send RFQ to Atlas Mill Ltda",
        cadence="weekly",
        priority="high",
        status="todo",
        source="agent",
        requires_evidence=True,
        acceptance_criteria="A quote from Atlas Mill Ltda is on file.",
    )
    sent = EmailMessage(
        direction="outbound",
        status="sent",
        to_email="sales@atlasmill.example.com",
        subject="RFQ — ICUMSA 45, 50,000 MT CFR Lagos",
        body="Please quote.",
        message_id="<rfq-1@atlas>",
    )
    session.add_all([task, sent])
    await session.flush()

    action = AgentAction(
        strategy_id=strategy.id,
        task_id=task.id,
        action_type="send_email",
        state="waiting_response",
        requires_approval=True,
        idempotency_key="k1",
        email_message_id=sent.id,
    )
    session.add(action)
    await session.commit()
    return action, task


async def test_agent_stays_parked_until_a_reply_actually_arrives(session):
    strategy = await _seed(session)
    action, _ = await _park_action_awaiting_reply(session, strategy)

    assert await orchestrator.resume_from_replies(session, strategy) == []
    await session.refresh(action)
    assert action.state == "waiting_response"


async def test_an_unrelated_inbound_email_does_not_resume_the_agent(session):
    strategy = await _seed(session)
    action, _ = await _park_action_awaiting_reply(session, strategy)
    session.add(
        EmailMessage(
            direction="inbound",
            status="received",
            from_email="someone@elsewhere.example.com",
            subject="Newsletter",
            body="Hello",
            in_reply_to="<some-other-thread@atlas>",
        )
    )
    await session.commit()

    assert await orchestrator.resume_from_replies(session, strategy) == []
    await session.refresh(action)
    assert action.state == "waiting_response"


async def test_a_reply_resumes_the_agent_and_leaves_evidence(session):
    strategy = await _seed(session)
    action, task = await _park_action_awaiting_reply(session, strategy)
    session.add(
        EmailMessage(
            direction="inbound",
            status="received",
            from_email="sales@atlasmill.example.com",
            subject="Re: RFQ — ICUMSA 45, 50,000 MT CFR Lagos",
            body="We can offer 50,000 MT at USD 430/MT CFR Lagos.",
            in_reply_to="<rfq-1@atlas>",
        )
    )
    await session.commit()

    resumed = await orchestrator.resume_from_replies(session, strategy)
    await session.commit()

    assert len(resumed) == 1
    await session.refresh(action)
    assert action.state == "in_progress"
    assert action.result["resumed_by"] == "reply"

    evidence = (await session.execute(Evidence.__table__.select())).mappings().all()
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "reply_received"
    assert evidence[0]["task_id"] == task.id
    assert "sales@atlasmill.example.com" in evidence[0]["description"]
