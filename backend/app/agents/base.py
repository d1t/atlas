"""Shared vocabulary for the orchestrator and the pillar agents.

The important type here is :class:`AgentContext`. Agents are handed a snapshot of the
*actual* pipeline — the real volume gap, the real named leads, the real stalled
conversations — and may only plan against it. There is no path by which an agent can
invent work, because :meth:`PillarAgent.decompose` receives no free-text prompt and
returns nothing when the context shows nothing to do.

That constraint is the whole point. A planner that always emits five plausible tasks is
indistinguishable from a planner that understands the business, right up until you act
on its output.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from app.models.deal import Deal
from app.models.opportunity import Opportunity
from app.models.strategy import Strategy, StrategyTask

#: What an agent is able to do. Anything not listed cannot be scheduled, so adding a
#: capability is a deliberate act rather than an emergent one.
CAPABILITIES = (
    "research_suppliers",   # run supplier discovery, qualify and rank the results
    "draft_email",          # produce a ready-to-send message for a specific counterparty
    "send_email",           # dispatch it, subject to the approval policy
    "analyse_reply",        # summarise an inbound reply and extract commitments
    "update_record",        # move a lead/deal stage to match what actually happened
    "prepare_brief",        # assemble a negotiation or credit brief from held data
    "flag_risk",            # raise a blocker for human attention
)


@dataclass(frozen=True)
class LeadView:
    """The subset of a lead an agent plans against."""

    id: int
    name: str
    email: str | None
    status: str
    volume_mt: float | None = None
    price_mt: float | None = None
    last_contacted_at: datetime | None = None
    opportunity_id: int | None = None

    @property
    def contactable(self) -> bool:
        return bool(self.email)


@dataclass(frozen=True)
class AgentContext:
    """A read-only snapshot of the strategy and its live pipeline."""

    strategy: Strategy
    opportunities: tuple[Opportunity, ...] = ()
    supplier_leads: tuple[LeadView, ...] = ()
    buyer_leads: tuple[LeadView, ...] = ()
    deals: tuple[Deal, ...] = ()
    existing_tasks: tuple[StrategyTask, ...] = ()
    now: datetime | None = None

    # --- Derived figures the agents plan against -------------------------------

    @property
    def target_volume_mt(self) -> float:
        return self.strategy.target_volume_mt or 0.0

    @property
    def committed_buy_volume_mt(self) -> float:
        """Volume buyers have actually committed to — not volume we have talked about."""
        return sum(
            lead.volume_mt or 0.0
            for lead in self.buyer_leads
            if lead.status == "committed"
        )

    @property
    def demand_gap_mt(self) -> float:
        return max(0.0, self.target_volume_mt - self.committed_buy_volume_mt)

    @property
    def shortlisted_supply_mt(self) -> float:
        return sum(
            opp.volume_mt or 0.0
            for opp in self.opportunities
            for lead in self.supplier_leads
            if lead.status == "shortlisted" and lead.opportunity_id == opp.id
        )

    @property
    def supply_gap_mt(self) -> float:
        return max(0.0, self.target_volume_mt - self.shortlisted_supply_mt)

    def open_task_titles(self) -> frozenset[str]:
        """Titles of work already on the board, so agents don't re-plan it."""
        return frozenset(
            t.title.strip().lower()
            for t in self.existing_tasks
            if t.status not in ("done", "dropped")
        )


@dataclass
class TaskSpec:
    """A node an agent wants to add to the strategy's task tree.

    ``acceptance_criteria`` and ``requires_evidence`` are not decoration: they are what
    stop the task being ticked off because an attempt was made. An agent that cannot
    state how its task would be proven finished should not be creating it.
    """

    title: str
    pillar: str
    kind: str = "task"
    detail: str | None = None
    acceptance_criteria: str | None = None
    requires_evidence: bool = False
    assignee: str = "agent"
    agent_key: str | None = None
    capability: str | None = None
    priority: str = "medium"
    confidence: float | None = None

    opportunity_id: int | None = None
    supplier_lead_id: int | None = None
    buyer_lead_id: int | None = None

    #: Sibling titles that must complete first; resolved to ids at persist time.
    depends_on_titles: tuple[str, ...] = ()
    children: list[TaskSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.capability is not None and self.capability not in CAPABILITIES:
            raise ValueError(f"Unknown capability: {self.capability}")
        if self.requires_evidence and not self.acceptance_criteria:
            raise ValueError(
                f"Task '{self.title}' is evidence-gated but states no acceptance "
                "criteria, so nobody could tell when it is genuinely done."
            )


class PillarAgent(ABC):
    """Base class for the four pillar agents.

    Subclasses are generic over strategy: nothing here knows about sugar, Brazil or
    Nigeria. They reason about gaps, stages and staleness, which is what makes the same
    machinery work for a different commodity or lane.
    """

    key: str
    pillar: str
    #: Capabilities this agent may use. The orchestrator refuses anything outside it.
    capabilities: tuple[str, ...] = ()

    @abstractmethod
    def decompose(self, ctx: AgentContext) -> list[TaskSpec]:
        """Return the work this pillar needs, given the pipeline as it stands.

        Must return ``[]`` when the context shows no gap. Returning plausible-looking
        filler is the specific failure this interface exists to prevent.
        """

    def _unseen(self, ctx: AgentContext, specs: list[TaskSpec]) -> list[TaskSpec]:
        """Drop anything already on the board, so re-running is not additive."""
        seen = ctx.open_task_titles()
        return [s for s in specs if s.title.strip().lower() not in seen]
