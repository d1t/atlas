from pydantic import BaseModel, ConfigDict, Field

from app.models.deal import DEAL_STAGES


class DealBase(BaseModel):
    title: str
    commodity: str
    volume_mt: float = 0.0
    buy_price: float = 0.0
    sell_price: float = 0.0
    freight_estimate: float = 0.0
    incoterms: str | None = None
    currency: str = "USD"
    supplier_id: int | None = None
    buyer_id: int | None = None
    notes: str | None = None


class DealCreate(DealBase):
    pass


class DealUpdate(BaseModel):
    title: str | None = None
    commodity: str | None = None
    volume_mt: float | None = None
    buy_price: float | None = None
    sell_price: float | None = None
    freight_estimate: float | None = None
    incoterms: str | None = None
    currency: str | None = None
    supplier_id: int | None = None
    buyer_id: int | None = None
    notes: str | None = None
    probability: int | None = None


class DealOut(DealBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage: str
    structure: str | None
    margin_per_mt: float
    total_value: float
    total_margin: float
    probability: int
    metrics: dict


class StageChange(BaseModel):
    stage: str = Field(..., pattern=f"^({'|'.join(DEAL_STAGES)})$")


class PricingInput(BaseModel):
    buy_price: float
    sell_price: float
    freight_estimate: float = 0.0
    volume_mt: float
    incoterms: str | None = None


class PricingResult(BaseModel):
    margin_per_mt: float
    total_value: float
    total_margin: float
    recommended_structure: str
    rationale: str
    scenarios: list[dict]


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    deal_id: int | None
    supplier_id: int | None
    user_id: int | None
    type: str
    message: str
    created_at: str | None = None


class ActivityCreate(BaseModel):
    message: str
    type: str = "note"


class TaskCreate(BaseModel):
    title: str
    due_at: str | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    deal_id: int | None
    title: str
    due_at: str | None = None
    done: bool
