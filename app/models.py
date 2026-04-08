from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="Mensaje del usuario.")
    thread_id: str | None = Field(default=None, description="Id de conversación.")
    context_hint: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    thread_id: str
    route: str
    answer: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class WebhookAck(BaseModel):
    ok: bool
    event_id: str | None = None
    lead_id: int | None = None
    buffered: bool = False
    reason: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    tenant: str
    integrations: dict[str, Any] = Field(default_factory=dict)
