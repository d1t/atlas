from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ScopeExplanationOut(BaseModel):
    scope: str
    reason: str


class ConsentOut(BaseModel):
    authorization_url: str
    scopes: list[ScopeExplanationOut] = Field(default_factory=list)


class ConnectionStatusOut(BaseModel):
    provider: str
    connected: bool
    #: "live" | "offline" | "needs_reconnect" | "unavailable"
    mode: str
    address: str = ""
    detail: str = ""
    fault: str | None = None
    missing_scopes: list[str] = Field(default_factory=list)
    can_send: bool = False
    connected_at: datetime | None = None
    last_used_at: datetime | None = None
