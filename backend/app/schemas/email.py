"""Pydantic schemas for the Gmail email layer."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class GmailStatus(BaseModel):
    configured: bool
    address: str | None = None
    mode: str  # "live" | "offline"


class EmailSendRequest(BaseModel):
    to_email: EmailStr
    subject: str
    body: str
    opportunity_id: int | None = None
    supplier_lead_id: int | None = None
    buyer_lead_id: int | None = None
    deal_id: int | None = None
    document_id: int | None = None
    # When replying, thread against a prior message.
    in_reply_to_message_id: int | None = None


class SendDocumentRequest(BaseModel):
    document_id: int
    to_email: EmailStr | None = None
    subject: str | None = None
    opportunity_id: int | None = None
    supplier_lead_id: int | None = None
    buyer_lead_id: int | None = None
    deal_id: int | None = None


class EmailMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    direction: str
    status: str
    opportunity_id: int | None
    supplier_lead_id: int | None
    buyer_lead_id: int | None
    deal_id: int | None
    document_id: int | None
    to_email: str | None
    from_email: str | None
    subject: str | None
    body: str
    message_id: str | None
    in_reply_to: str | None
    matched_side: str | None
    sent_at: datetime | None
    received_at: datetime | None
    error: str | None
    created_at: datetime | None


class ReplySyncResult(BaseModel):
    fetched: int
    matched: int
    new_messages: list[EmailMessageOut]
    mode: str  # "live" | "offline"
