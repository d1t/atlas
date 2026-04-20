from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DOCUMENT_TYPES


class DocumentGenerateRequest(BaseModel):
    type: str = Field(..., pattern=f"^({'|'.join(DOCUMENT_TYPES)})$")
    deal_id: int | None = None
    supplier_id: int | None = None
    inputs: dict = Field(default_factory=dict)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    content: str
    inputs: dict
    deal_id: int | None
    supplier_id: int | None


class DocumentUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
