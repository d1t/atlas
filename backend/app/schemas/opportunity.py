"""Pydantic schemas for the V2 opportunity-centric API."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.opportunity import (
    APPETITE_LEVELS,
    BUYER_LEAD_STATUSES,
    OPPORTUNITY_STATUSES,
    SUPPLIER_LEAD_STATUSES,
    URGENCY_LEVELS,
)

_OPP_STATUS_PATTERN = f"^({'|'.join(OPPORTUNITY_STATUSES)})$"
_SUP_STATUS_PATTERN = f"^({'|'.join(SUPPLIER_LEAD_STATUSES)})$"
_BUY_STATUS_PATTERN = f"^({'|'.join(BUYER_LEAD_STATUSES)})$"
_APPETITE_PATTERN = f"^({'|'.join(APPETITE_LEVELS)})$"
_URGENCY_PATTERN = f"^({'|'.join(URGENCY_LEVELS)})$"


# --- Opportunity ----------------------------------------------------------------


class OpportunityBase(BaseModel):
    title: str
    commodity: str
    volume_mt: float = 0.0
    destination_country: str | None = None
    destination_port: str | None = None
    incoterms: str | None = None
    target_price_min: float | None = None
    target_price_max: float | None = None
    currency: str = "USD"
    notes: str | None = None


class OpportunityCreate(OpportunityBase):
    pass


class OpportunityUpdate(BaseModel):
    title: str | None = None
    commodity: str | None = None
    volume_mt: float | None = None
    destination_country: str | None = None
    destination_port: str | None = None
    incoterms: str | None = None
    target_price_min: float | None = None
    target_price_max: float | None = None
    currency: str | None = None
    notes: str | None = None
    status: str | None = Field(default=None, pattern=_OPP_STATUS_PATTERN)


class OpportunityOut(OpportunityBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    owner_id: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# --- SupplierLead ---------------------------------------------------------------


class SupplierLeadBase(BaseModel):
    supplier_id: int | None = None
    supplier_name: str | None = None
    country: str | None = None
    email: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    price_mt: float | None = None
    quoted_incoterms: str | None = None
    min_order_mt: float | None = None
    lead_time_days: int | None = None
    payment_terms: str | None = None
    credibility_score: int = 50
    responsiveness_score: int = 50
    notes: str | None = None


class SupplierLeadCreate(SupplierLeadBase):
    pass


class SupplierLeadUpdate(BaseModel):
    supplier_id: int | None = None
    supplier_name: str | None = None
    country: str | None = None
    email: str | None = None
    contact_name: str | None = None
    contact_title: str | None = None
    price_mt: float | None = None
    quoted_incoterms: str | None = None
    min_order_mt: float | None = None
    lead_time_days: int | None = None
    payment_terms: str | None = None
    credibility_score: int | None = None
    responsiveness_score: int | None = None
    last_contacted_at: datetime | None = None
    status: str | None = Field(default=None, pattern=_SUP_STATUS_PATTERN)
    notes: str | None = None
    negotiation_stage: int | None = Field(default=None, ge=1, le=5)
    intel: dict | None = None
    disclosed: dict | None = None


class SupplierLeadOut(SupplierLeadBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    opportunity_id: int
    status: str
    last_contacted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    negotiation_stage: int = 1
    intel: dict = {}
    disclosed: dict = {}


# --- BuyerLead ------------------------------------------------------------------


class BuyerLeadBase(BaseModel):
    buyer_id: int | None = None
    buyer_name: str | None = None
    country: str | None = None
    email: str | None = None
    target_price_mt: float | None = None
    volume_mt: float | None = None
    appetite: str = Field(default="medium", pattern=_APPETITE_PATTERN)
    urgency: str = Field(default="medium", pattern=_URGENCY_PATTERN)
    feedback: str | None = None
    notes: str | None = None


class BuyerLeadCreate(BuyerLeadBase):
    pass


class BuyerLeadUpdate(BaseModel):
    buyer_id: int | None = None
    buyer_name: str | None = None
    country: str | None = None
    email: str | None = None
    target_price_mt: float | None = None
    volume_mt: float | None = None
    appetite: str | None = Field(default=None, pattern=_APPETITE_PATTERN)
    urgency: str | None = Field(default=None, pattern=_URGENCY_PATTERN)
    last_contacted_at: datetime | None = None
    feedback: str | None = None
    status: str | None = Field(default=None, pattern=_BUY_STATUS_PATTERN)
    notes: str | None = None
    negotiation_stage: int | None = Field(default=None, ge=1, le=5)
    intel: dict | None = None
    disclosed: dict | None = None


class BuyerLeadOut(BuyerLeadBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    opportunity_id: int
    status: str
    last_contacted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    negotiation_stage: int = 1
    intel: dict = {}
    disclosed: dict = {}


# --- Derived: match + health + next-action --------------------------------------


class MatchPair(BaseModel):
    supplier_lead_id: int
    supplier_name: str | None
    supplier_price_mt: float | None
    buyer_lead_id: int
    buyer_name: str | None
    buyer_target_price_mt: float | None
    margin_per_mt: float
    total_margin: float | None
    score: float  # 0-100
    reasoning: list[str]


class MatchingResult(BaseModel):
    opportunity_id: int
    total_pairs: int
    viable_pairs: int
    pairs: list[MatchPair]


class HealthFactor(BaseModel):
    name: str
    weight: float
    value: float  # 0-1
    contribution: float  # weight * value * 100, capped
    detail: str


class HealthScore(BaseModel):
    opportunity_id: int
    score: int  # 0-100
    status: str  # "viable" / "moderate" / "weak"
    factors: list[HealthFactor]
    recommendation: str


class NextAction(BaseModel):
    action: str
    priority: str  # "high" / "medium" / "low"
    reasoning: str


class NextActionsOut(BaseModel):
    opportunity_id: int
    actions: list[NextAction]


class PromoteMatchRequest(BaseModel):
    supplier_lead_id: int
    buyer_lead_id: int
    title: str | None = None


class OpportunityDashboard(BaseModel):
    """One-stop fetch for the opportunity workspace UI."""

    opportunity: OpportunityOut
    supplier_leads: list[SupplierLeadOut]
    buyer_leads: list[BuyerLeadOut]
    matches: MatchingResult
    health: HealthScore
    next_actions: NextActionsOut


# --- Curated counterparties ---------------------------------------------------


class CuratedCounterpartyOut(BaseModel):
    """A pre-vetted counterparty surfaced for a known commodity/origin lane."""

    name: str
    country: str
    commodity: str
    website: str
    type: str
    description: str
    already_added: bool = False


class CuratedSeedRequest(BaseModel):
    """Request to seed selected curated counterparties as supplier leads.

    When ``names`` is empty / omitted the server seeds the full matching set;
    when non-empty only the named entries are seeded. Names are matched
    case-insensitively against ``CuratedCounterparty.name``.
    """

    names: list[str] = []
