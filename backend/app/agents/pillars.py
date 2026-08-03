"""The four pillar agents.

Each one answers a single question about the live pipeline and produces work only when
the answer is unsatisfactory:

* origination — are there enough live opportunities to hit the target at all?
* demand      — how much volume is still uncommitted by buyers?
* supply      — how much volume is still unsecured from suppliers?
* execution   — which agreed deals are not moving towards contract and shipment?

None of them know what a commodity is. They reason about gaps, stages and staleness.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agents.base import AgentContext, LeadView, PillarAgent, TaskSpec

#: A conversation with no contact for this long is stalled, not in progress.
STALE_AFTER = timedelta(days=7)

#: Below this many live opportunities a strategy has no realistic route to its target.
MIN_LIVE_OPPORTUNITIES = 2


def _is_stale(lead: LeadView, now: datetime) -> bool:
    if lead.last_contacted_at is None:
        return False
    last = lead.last_contacted_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last > STALE_AFTER


def _fmt_mt(value: float) -> str:
    return f"{value:,.0f} MT"


class OriginationAgent(PillarAgent):
    key = "origination_agent"
    pillar = "origination"
    capabilities = ("research_suppliers", "prepare_brief", "draft_email")

    def decompose(self, ctx: AgentContext) -> list[TaskSpec]:
        live = [o for o in ctx.opportunities if o.status not in ("closed", "lost")]
        if len(live) >= MIN_LIVE_OPPORTUNITIES:
            return []

        needed = MIN_LIVE_OPPORTUNITIES - len(live)
        lane = " to ".join(
            p
            for p in (ctx.strategy.origin_region, ctx.strategy.destination_region)
            if p
        )
        commodity = ctx.strategy.commodity or "the target commodity"

        outcome = TaskSpec(
            title=f"Open {needed} more live {commodity} opportunity(s)",
            pillar=self.pillar,
            kind="outcome",
            detail=(
                f"Only {len(live)} live opportunity(s) against a target of "
                f"{_fmt_mt(ctx.target_volume_mt)}. One lane cannot absorb the target "
                "and leaves the strategy dependent on a single counterparty."
            ),
            acceptance_criteria=(
                f"At least {MIN_LIVE_OPPORTUNITIES} opportunities exist with a named "
                "destination, volume and target price, none in draft."
            ),
            requires_evidence=True,
            agent_key=self.key,
            priority="high",
            children=[
                TaskSpec(
                    title=f"Map credible {commodity} supply origins{f' for {lane}' if lane else ''}",
                    pillar=self.pillar,
                    kind="task",
                    capability="research_suppliers",
                    agent_key=self.key,
                    detail=(
                        "Run supplier discovery for the lane and rank by credibility "
                        "to establish whether the origin can actually support the "
                        "target volume."
                    ),
                    acceptance_criteria=(
                        "At least 4 qualified origin candidates recorded with "
                        "credibility scores."
                    ),
                    requires_evidence=True,
                ),
                TaskSpec(
                    title="Draft the lane thesis brief",
                    pillar=self.pillar,
                    kind="task",
                    capability="prepare_brief",
                    agent_key=self.key,
                    detail=(
                        "Price spread, freight, tariff exposure and the margin case "
                        "at the strategy's target of "
                        f"${ctx.strategy.target_margin_per_mt or 0:,.0f}/MT."
                    ),
                    depends_on_titles=(
                        f"Map credible {commodity} supply origins"
                        f"{f' for {lane}' if lane else ''}",
                    ),
                ),
            ],
        )
        return self._unseen(ctx, [outcome])


class DemandAgent(PillarAgent):
    key = "demand_agent"
    pillar = "demand"
    capabilities = ("draft_email", "send_email", "analyse_reply", "update_record")

    def decompose(self, ctx: AgentContext) -> list[TaskSpec]:
        gap = ctx.demand_gap_mt
        if gap <= 0:
            return []

        now = ctx.now or datetime.now(UTC)
        specs: list[TaskSpec] = []

        outcome = TaskSpec(
            title=f"Secure off-take for the remaining {_fmt_mt(gap)}",
            pillar=self.pillar,
            kind="outcome",
            detail=(
                f"{_fmt_mt(ctx.committed_buy_volume_mt)} of "
                f"{_fmt_mt(ctx.target_volume_mt)} is committed by buyers."
            ),
            acceptance_criteria=(
                "Signed or countersigned off-take covering the outstanding volume is "
                "on file. Buyer interest, meetings and verbal indications do not count."
            ),
            requires_evidence=True,
            agent_key=self.key,
            priority="high",
        )

        # One milestone per real buyer conversation that needs moving, named.
        for lead in ctx.buyer_leads:
            if lead.status in ("committed", "declined", "lost"):
                continue

            if lead.status == "new":
                title = f"Open the conversation with {lead.name}"
                criteria = (
                    f"{lead.name} has replied, or three approaches have been logged "
                    "with no response."
                )
                capability = "draft_email"
            elif _is_stale(lead, now):
                title = f"Revive the stalled {lead.name} conversation"
                criteria = (
                    f"A reply from {lead.name} is recorded, or the lead is marked lost "
                    "with a reason."
                )
                capability = "draft_email"
            else:
                title = f"Convert {lead.name} from engaged to committed volume"
                criteria = (
                    f"{lead.name} has confirmed a volume and price in writing."
                )
                capability = "prepare_brief" if lead.contactable else "flag_risk"

            child = TaskSpec(
                title=title,
                pillar=self.pillar,
                kind="milestone",
                detail=(
                    f"Status: {lead.status}"
                    + (f", indicated {_fmt_mt(lead.volume_mt)}" if lead.volume_mt else "")
                    + ("" if lead.contactable else ". No email address on file.")
                ),
                acceptance_criteria=criteria,
                requires_evidence=True,
                agent_key=self.key,
                buyer_lead_id=lead.id,
                opportunity_id=lead.opportunity_id,
                capability=capability if capability in self.capabilities else None,
                priority="high" if lead.status == "engaged" else "medium",
            )
            if not lead.contactable:
                child.assignee = "human"
                child.capability = None
                child.detail = (
                    f"{child.detail} An agent cannot progress this without a contact "
                    "address."
                )
            outcome.children.append(child)

        # No named buyers at all: the gap is a prospecting problem, not a chasing one.
        if not outcome.children:
            outcome.children.append(
                TaskSpec(
                    title=f"Build a buyer list for {_fmt_mt(gap)}",
                    pillar=self.pillar,
                    kind="task",
                    detail=(
                        "No buyer leads exist, so there is nobody to follow up. "
                        "Identify and qualify importers in "
                        f"{ctx.strategy.destination_region or 'the destination market'}."
                    ),
                    acceptance_criteria=(
                        "At least 5 qualified buyer leads with contact details exist."
                    ),
                    requires_evidence=True,
                    agent_key=self.key,
                    priority="high",
                )
            )

        specs.append(outcome)
        return self._unseen(ctx, specs)


class SupplyAgent(PillarAgent):
    key = "supply_agent"
    pillar = "supply"
    capabilities = (
        "research_suppliers",
        "draft_email",
        "send_email",
        "analyse_reply",
        "update_record",
    )

    def decompose(self, ctx: AgentContext) -> list[TaskSpec]:
        gap = ctx.supply_gap_mt
        if gap <= 0:
            return []

        quoted = [lead for lead in ctx.supplier_leads if lead.status == "quoted"]
        contactable_new = [
            lead
            for lead in ctx.supplier_leads
            if lead.status == "new" and lead.contactable
        ]

        outcome = TaskSpec(
            title=f"Secure supply cover for {_fmt_mt(gap)}",
            pillar=self.pillar,
            kind="outcome",
            detail=(
                f"{_fmt_mt(ctx.shortlisted_supply_mt)} shortlisted against a target of "
                f"{_fmt_mt(ctx.target_volume_mt)}."
            ),
            acceptance_criteria=(
                "Shortlisted suppliers with firm quotes cover the outstanding volume. "
                "Adding a supplier to a list does not count."
            ),
            requires_evidence=True,
            agent_key=self.key,
            priority="high",
        )

        # Only run discovery when the existing funnel genuinely cannot cover the gap.
        if len(quoted) + len(contactable_new) < 3:
            outcome.children.append(
                TaskSpec(
                    title="Discover and qualify additional suppliers",
                    pillar=self.pillar,
                    kind="task",
                    capability="research_suppliers",
                    agent_key=self.key,
                    detail=(
                        f"Only {len(quoted)} quoted and {len(contactable_new)} "
                        "contactable new supplier(s) on file — not enough to cover the "
                        "gap or to create price tension."
                    ),
                    acceptance_criteria=(
                        "At least 4 ranked candidates recorded, each with a "
                        "credibility score and a contact route."
                    ),
                    requires_evidence=True,
                    priority="high",
                )
            )

        for lead in contactable_new[:4]:
            outcome.children.append(
                TaskSpec(
                    title=f"Send RFQ to {lead.name}",
                    pillar=self.pillar,
                    kind="task",
                    capability="draft_email",
                    agent_key=self.key,
                    supplier_lead_id=lead.id,
                    opportunity_id=lead.opportunity_id,
                    detail="Request specification, price, incoterms and loading window.",
                    acceptance_criteria=(
                        f"A quote from {lead.name} is on file, or the lead is closed "
                        "with a reason."
                    ),
                    requires_evidence=True,
                )
            )

        for lead in quoted:
            outcome.children.append(
                TaskSpec(
                    title=f"Verify and shortlist {lead.name}",
                    pillar=self.pillar,
                    kind="milestone",
                    agent_key=self.key,
                    supplier_lead_id=lead.id,
                    opportunity_id=lead.opportunity_id,
                    detail=(
                        f"Quoted{f' at ${lead.price_mt:,.0f}/MT' if lead.price_mt else ''}. "
                        "Confirm the counterparty is real before relying on the price."
                    ),
                    acceptance_criteria=(
                        "Company registration, trade references and specification "
                        "verified, and the lead moved to shortlisted."
                    ),
                    requires_evidence=True,
                    capability="prepare_brief"
                    if "prepare_brief" in self.capabilities
                    else None,
                )
            )

        return self._unseen(ctx, [outcome])


class ExecutionAgent(PillarAgent):
    key = "execution_agent"
    pillar = "execution"
    capabilities = ("prepare_brief", "draft_email", "analyse_reply", "flag_risk")

    #: What has to be true before a deal at this stage can move on.
    STAGE_GATES: dict[str, tuple[str, str]] = {
        "buyer_matched": (
            "Agree commercial terms and issue the SPA",
            "A draft SPA has been issued to both sides and terms are agreed in writing.",
        ),
        "spa": (
            "Get the SPA executed",
            "A countersigned SPA is on file.",
        ),
        "lc": (
            "Get a workable LC issued",
            "An LC matching the SPA terms is issued and checked for discrepancies.",
        ),
        "shipment": (
            "Complete shipment and present documents",
            "Bill of lading and full document set presented and accepted.",
        ),
    }

    def decompose(self, ctx: AgentContext) -> list[TaskSpec]:
        specs: list[TaskSpec] = []
        for deal in ctx.deals:
            gate = self.STAGE_GATES.get(deal.stage)
            if gate is None:
                continue
            title, criteria = gate
            specs.append(
                TaskSpec(
                    title=f"{title} — {deal.title}",
                    pillar=self.pillar,
                    kind="outcome",
                    detail=(
                        f"Deal at stage '{deal.stage}', "
                        f"{_fmt_mt(deal.volume_mt or 0.0)} at "
                        f"${deal.margin_per_mt or 0.0:,.0f}/MT margin."
                    ),
                    acceptance_criteria=criteria,
                    requires_evidence=True,
                    agent_key=self.key,
                    priority="high",
                    opportunity_id=deal.opportunity_id,
                    capability="prepare_brief",
                )
            )
        return self._unseen(ctx, specs)


#: Instantiated once; they hold no state between runs.
PILLAR_AGENTS: tuple[PillarAgent, ...] = (
    OriginationAgent(),
    DemandAgent(),
    SupplyAgent(),
    ExecutionAgent(),
)
