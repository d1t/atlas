"""Schemas for the execution spine — task trees, agent activity, approvals, evidence."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EvidenceCreate(BaseModel):
    kind: str = Field(description="One of EVIDENCE_KINDS")
    description: str
    email_message_id: int | None = None
    document_id: int | None = None
    opportunity_id: int | None = None
    payload: dict = Field(default_factory=dict)


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    kind: str
    description: str
    email_message_id: int | None = None
    document_id: int | None = None
    opportunity_id: int | None = None
    created_by_type: str
    created_at: datetime


class AgentActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int | None = None
    strategy_id: int
    task_id: int | None = None
    action_type: str
    state: str
    requires_approval: bool
    payload: dict
    result: dict
    rationale: str | None = None
    attempts: int
    max_attempts: int
    last_error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    pillar: str | None = None
    agent_key: str
    trigger: str
    status: str
    summary: str | None = None
    reasoning: str | None = None
    confidence: float | None = None
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action_id: int
    strategy_id: int
    status: str
    risk: str
    request_summary: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    created_at: datetime


class ApprovalQueueItem(BaseModel):
    """An approval joined with enough of its action to decide without a second call."""

    approval: ApprovalOut
    action: AgentActionOut
    task_title: str | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    reason: str | None = None


class PlanRunOut(BaseModel):
    """Result of an orchestrator planning run."""

    run: AgentRunOut
    created_task_ids: list[int] = Field(default_factory=list)


class GrantCreate(BaseModel):
    """Request a narrow standing authorisation for repeat follow-ups on one thread."""

    action_type: str = Field(default="send_email")
    thread_key: str
    recipient: str
    template_key: str
    #: Bind the grant to specific wording; any material rewrite then needs approval.
    approved_body: str | None = None
    max_messages: int = Field(default=3, ge=1, le=10)
    expires_in_days: int = Field(default=14, ge=1, le=90)


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    action_type: str
    thread_key: str
    recipient: str
    template_key: str
    max_messages: int
    used_count: int
    expires_at: datetime
    paused: bool
    revoked_at: datetime | None = None
    created_at: datetime


class GrantPauseRequest(BaseModel):
    paused: bool


class PolicyPreviewRequest(BaseModel):
    """Ask what would happen to a draft without creating anything."""

    action_type: str = Field(default="send_email")
    recipient: str
    subject: str = ""
    body: str
    thread_key: str = ""
    template_key: str = ""
    has_attachments: bool = False
    materially_changed: bool = False


class PolicyPreviewOut(BaseModel):
    requires_approval: bool
    reason: str
    risk: str
    grant_id: int | None = None
    triggers: list[str] = Field(default_factory=list)


class TaskNode(BaseModel):
    """A task plus its children, so the UI can render the tree in one pass."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    parent_id: int | None = None
    kind: str
    pillar: str
    title: str
    detail: str | None = None
    status: str
    priority: str
    position: int
    assignee: str
    agent_key: str | None = None
    confidence: float | None = None
    blocked_reason: str | None = None
    acceptance_criteria: str | None = None
    requires_evidence: bool
    override_reason: str | None = None
    depends_on_ids: list[int] = Field(default_factory=list)
    due_at: datetime | None = None
    completed_at: datetime | None = None
    verified_at: datetime | None = None

    evidence_count: int = 0
    #: Prerequisite tasks that are not yet done — the reason this task can't start.
    blocked_by: list[int] = Field(default_factory=list)
    children: list[TaskNode] = Field(default_factory=list)


class TaskCompleteRequest(BaseModel):
    #: Required only when the task is gated on evidence and none exists.
    override_reason: str | None = None


class DecomposeRequest(BaseModel):
    """Ask an agent to break a task into an executable tree."""

    max_steps: int = Field(default=12, ge=2, le=30)


class KpiSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    pillar: str
    kpi_key: str
    value: float
    delta: float | None = None
    source_task_id: int | None = None
    note: str | None = None
    created_at: datetime


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int | None = None
    actor_type: str
    actor_label: str | None = None
    action: str
    entity_type: str
    entity_id: int | None = None
    before: dict
    after: dict
    created_at: datetime


TaskNode.model_rebuild()
