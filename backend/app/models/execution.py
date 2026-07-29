"""Execution spine — the records that make strategy execution *provable*.

The strategy engine could already describe work: a :class:`~app.models.strategy.StrategyTask`
was a row with a checkbox. It could not show that the work actually happened, who or what did
it, or why a KPI moved. These models close that gap:

``AgentRun``      one invocation of an agent, with its reasoning and confidence.
``AgentAction``   a single discrete thing an agent proposes or performs, with an explicit
                  lifecycle and an idempotency key so the same action cannot fire twice.
``Approval``      the human gate in front of anything external or commercial.
``Evidence``      the artefact that justifies calling a task done.
``KpiSnapshot``   an append-only trail making KPI movement traceable to the task that caused it.
``AuditLog``      who changed what, when.

Nothing here is specific to the sugar value chain: ``pillar`` and ``agent_key`` are plain
strings, so a strategy with entirely different pillars gets the same machinery.
"""
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# --- Agent run lifecycle ---------------------------------------------------

AGENT_RUN_STATUSES = ["running", "completed", "failed", "cancelled"]

# What kicked the agent off, so a user can tell scheduled work from work they asked for.
AGENT_TRIGGERS = ["manual", "schedule", "reply", "replan", "dependency"]


# --- Agent action lifecycle ------------------------------------------------

# The full execution state machine. Kept as one list so the API, the UI and the
# database all agree on the vocabulary.
AGENT_ACTION_STATES = [
    "proposed",
    "awaiting_approval",
    "approved",
    "queued",
    "in_progress",
    "waiting_response",
    "waiting_human",
    "blocked",
    "failed",
    "retrying",
    "completed",
    "rejected",
    "cancelled",
]

#: States from which an action will never progress on its own.
TERMINAL_ACTION_STATES = frozenset({"completed", "rejected", "cancelled"})

#: States that mean "a human has to do something before this can move".
HUMAN_BLOCKED_STATES = frozenset({"awaiting_approval", "waiting_human", "blocked"})

#: Action types an agent may perform without asking. Everything not listed here
#: needs an :class:`Approval` before it can leave ``proposed``.
AUTONOMOUS_ACTION_TYPES = frozenset(
    {
        "research",
        "analyse_records",
        "analyse_replies",
        "decompose_task",
        "draft_email",
        "prepare_brief",
        "summarise_thread",
        "update_internal_record",
        "propose_followup",
        "flag_risk",
    }
)

#: Action types that always require a human decision, however the agent is configured.
#: These either leave the system, commit money, or destroy data.
ALWAYS_APPROVED_ACTION_TYPES = frozenset(
    {
        "send_email",
        "accept_terms",
        "commit_funds",
        "sign_document",
        "delete_data",
        "share_confidential",
        "change_strategy_target",
    }
)

APPROVAL_STATUSES = ["pending", "approved", "rejected", "expired"]
RISK_LEVELS = ["low", "medium", "high"]

# --- Evidence --------------------------------------------------------------

EVIDENCE_KINDS = [
    "email_sent",
    "reply_received",
    "document_signed",
    "document_generated",
    "record_link",
    "kpi_delta",
    "manual_note",
]

ACTOR_TYPES = ["agent", "human", "system"]


class AgentRun(Base, TimestampMixin):
    """One invocation of an orchestrator or pillar agent."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True, nullable=False
    )

    #: ``None`` for the orchestrator; otherwise the pillar this agent owns.
    pillar: Mapped[str | None] = mapped_column(String(32), index=True)
    #: Stable identifier for the agent implementation, e.g. ``"orchestrator"``.
    agent_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    trigger: Mapped[str] = mapped_column(
        String(16), default="manual", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default="running", index=True, nullable=False
    )

    #: Plain-language explanation shown to the user — required for every run so the
    #: interface can always answer "what did the agent just do, and why?".
    summary: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class AgentAction(Base, TimestampMixin):
    """A single discrete action, from proposal through to a terminal state."""

    __tablename__ = "agent_actions"
    __table_args__ = (
        # Two agents working the same pillar must not both fire the same action.
        UniqueConstraint("idempotency_key", name="uq_agent_actions_idempotency"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_tasks.id", ondelete="CASCADE"), index=True
    )

    action_type: Mapped[str] = mapped_column(String(48), index=True, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), default="proposed", index=True, nullable=False
    )

    #: Set at creation from the action type + the strategy's permission settings.
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    #: What the action will do (e.g. recipient/subject/body for a draft).
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    #: What happened when it ran.
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    #: Why the agent wants to do this — surfaced in the approval queue.
    rationale: Mapped[str | None] = mapped_column(Text)

    #: Deterministic hash of (strategy, action type, target). Deduplicates retries
    #: and stops repeat outreach to the same counterparty.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Populated once an action produces a real artefact.
    email_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id", ondelete="SET NULL")
    )


class Approval(Base, TimestampMixin):
    """A human decision gate in front of one :class:`AgentAction`."""

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_approvals_action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[int] = mapped_column(
        ForeignKey("agent_actions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True, nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True, nullable=False
    )
    risk: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)

    #: Human-readable statement of what is being approved, captured at request time
    #: so the queue stays meaningful even if the action payload is later edited.
    request_summary: Mapped[str | None] = mapped_column(Text)

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decision_reason: Mapped[str | None] = mapped_column(Text)


class Evidence(Base, TimestampMixin):
    """An artefact justifying a task's completion.

    A task with ``requires_evidence`` cannot be completed without at least one of
    these, which is what stops "I sent an email" from counting as "I secured a buyer".
    """

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("strategy_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )

    kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    email_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_messages.id", ondelete="SET NULL")
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id", ondelete="SET NULL")
    )

    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_by_type: Mapped[str] = mapped_column(
        String(16), default="agent", nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class KpiSnapshot(Base, TimestampMixin):
    """Append-only KPI history, so progress is traceable to the task that caused it."""

    __tablename__ = "kpi_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pillar: Mapped[str] = mapped_column(String(32), index=True, nullable=False)

    kpi_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    #: Change versus the previous snapshot for this KPI; ``None`` for the first.
    delta: Mapped[float | None] = mapped_column(Float)

    #: The completed task this movement is attributed to, when there is one.
    source_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategy_tasks.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base, TimestampMixin):
    """Who changed what. Written for every state transition an agent makes."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Agent key when ``actor_type == "agent"``.
    actor_label: Mapped[str | None] = mapped_column(String(64))

    action: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)

    before: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
