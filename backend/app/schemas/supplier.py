from pydantic import BaseModel, ConfigDict, Field


class SupplierBase(BaseModel):
    name: str
    type: str | None = None
    country: str | None = None
    commodity: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    contact_name: str | None = None
    description: str | None = None
    source: str | None = None


class SupplierCreate(SupplierBase):
    pass


class SupplierUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    country: str | None = None
    commodity: str | None = None
    website: str | None = None
    email: str | None = None
    phone: str | None = None
    contact_name: str | None = None
    description: str | None = None


class SupplierOut(SupplierBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    credibility_score: int
    risk_score: int
    red_flags: list[str]
    classification_confidence: float
    extra_data: dict


class DiscoveryRequest(BaseModel):
    commodity: str = Field(..., min_length=1)
    country: str | None = None
    limit: int = Field(10, ge=1, le=50)


class ClassificationResult(BaseModel):
    type: str
    confidence: float
    reasoning: str
