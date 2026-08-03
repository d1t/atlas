"""Pydantic schemas for the strategy engine."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.strategy import (
    PILLARS,
    STRATEGY_STATUSES,
    TASK_CADENCES,
    TASK_PRIORITIES,
    TASK_STATUSES,
)
from app.schemas.email import EmailMessageOut

_STATUS_PATTERN = f"^({'|'.join(STRATEGY_STATUSES)})$"
_PILLAR_PATTERN = f"^({'|'.join(PILLARS)})$"
_TASK_STATUS_PATTERN = f"^({'|'.join(TASK_STATUSES)})$"
_CADENCE_PATTERN = f"^({'|'.join(TASK_CADENCES)})$"
_PRIORITY_PATTERN = f"^({'|'.join(TASK_PRIORITIES)})$"


class StrategyBase(BaseModel):
    title: str
    north_star: str | None = None
    commodity: str | None = None
    origin_region: str | None = None
    destination_region: str | None = None
    horizon: str = "quarter"
    target_volume_mt: float | None = None
    target_margin_per_mt: float | None = None


class StrategyCreate(StrategyBase):
    # When true, the AI planner drafts the four-pillar objectives on create.
    auto_plan: bool = True


class StrategyUpdate(BaseModel):
    title: str | None = None
    north_star: str | None = None
    commodity: str | None = None
    origin_region: str | None = None
    destination_region: str | None = None
    horizon: str | None = None
    target_volume_mt: float | None = None
    target_margin_per_mt: float | None = None
    pillars: dict | None = None
    status: str | None = Field(default=None, pattern=_STATUS_PATTERN)


class StrategyOut(StrategyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pillars: dict
    status: str
    agents_paused: bool = False
    agents_paused_reason: str | None = None
    owner_id: int | None
    created_at: datetime | None
    updated_at: datetime | None


class StrategyTaskCreate(BaseModel):
    pillar: str = Field(pattern=_PILLAR_PATTERN)
    title: str
    detail: str | None = None
    cadence: str = Field(default="weekly", pattern=_CADENCE_PATTERN)
    priority: str = Field(default="medium", pattern=_PRIORITY_PATTERN)
    week_start: date | None = None
    due_at: datetime | None = None
    opportunity_id: int | None = None


class StrategyTaskUpdate(BaseModel):
    title: str | None = None
    detail: str | None = None
    pillar: str | None = Field(default=None, pattern=_PILLAR_PATTERN)
    cadence: str | None = Field(default=None, pattern=_CADENCE_PATTERN)
    priority: str | None = Field(default=None, pattern=_PRIORITY_PATTERN)
    status: str | None = Field(default=None, pattern=_TASK_STATUS_PATTERN)
    due_at: datetime | None = None


class StrategyTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    strategy_id: int
    pillar: str
    title: str
    detail: str | None
    cadence: str
    priority: str
    status: str
    week_start: date | None
    due_at: datetime | None
    opportunity_id: int | None
    supplier_lead_id: int | None
    buyer_lead_id: int | None
    source: str
    completed_at: datetime | None
    created_at: datetime | None


class PillarProgress(BaseModel):
    pillar: str
    label: str
    objective: str | None
    kpi: str | None
    target: float | None
    actual: float
    progress_pct: float
    tasks_total: int
    tasks_done: int
    status: str  # "on_track" | "at_risk" | "behind" | "idle"
    detail: str


class GeneratePlanRequest(BaseModel):
    week_start: date | None = None


class StrategyBoard(BaseModel):
    strategy: StrategyOut
    week_start: date
    pillars: list[PillarProgress]
    week_tasks: list[StrategyTaskOut]
    today_tasks: list[StrategyTaskOut]
    headline: str


class DigestRequest(BaseModel):
    # Where to send the weekly plan; defaults to the configured Gmail address.
    to_email: EmailStr | None = None
    week_start: date | None = None


class DigestResult(BaseModel):
    subject: str
    mode: str  # "live" | "offline"
    message: EmailMessageOut


class TaskEmailDraft(BaseModel):
    """A review-ready email drafted for a single strategy task."""

    task_id: int
    to_email: str | None
    to_name: str | None
    subject: str
    body: str
    opportunity_id: int | None
    supplier_lead_id: int | None
    buyer_lead_id: int | None
    mode: str  # "live" | "offline"
    can_send: bool
    reason: str | None = None


class TaskEmailSend(BaseModel):
    to_email: EmailStr
    subject: str
    body: str
    # Tick the task off once the send succeeds / is offline-recorded.
    complete_task: bool = True


class TaskEmailResult(BaseModel):
    mode: str  # "live" | "offline"
    message: EmailMessageOut
    task: StrategyTaskOut


class SourcingCandidate(BaseModel):
    """A discovered, qualified & ranked supplier with a ready-to-send RFQ."""

    name: str
    country: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    contact_name: str | None = None
    type: str | None = None
    source: str | None = None
    description: str | None = None
    credibility_score: int
    risk_score: int
    red_flags: list[str] = Field(default_factory=list)
    subject: str
    body: str
    can_send: bool
    reason: str | None = None


class SourcingResult(BaseModel):
    task_id: int
    opportunity_id: int | None
    commodity: str
    country: str | None
    mode: str  # "live" | "offline"
    candidates: list[SourcingCandidate]


class SourcingEmailSend(BaseModel):
    to_email: EmailStr
    subject: str
    body: str
    supplier_name: str
    country: str | None = None
    website: str | None = None
    contact_name: str | None = None
    # A sourcing task fans out to several candidates, so default to *not*
    # ticking it off on each individual send.
    complete_task: bool = False
