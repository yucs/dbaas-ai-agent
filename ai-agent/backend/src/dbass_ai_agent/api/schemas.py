from __future__ import annotations

from pydantic import BaseModel, Field

from dbass_ai_agent.sessions.models import (
    ApprovalRecord,
    ChatMessage,
    SessionDetail,
    SessionIndexItem,
    SessionMeta,
)


class CreateSessionRequest(BaseModel):
    title: str | None = None


class SessionListResponse(BaseModel):
    items: list[SessionIndexItem] = Field(default_factory=list)


class SessionResponse(BaseModel):
    session: SessionDetail


class SessionMetaResponse(BaseModel):
    session: SessionMeta


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool = True


class SendMessageRequest(BaseModel):
    content: str


class SendMessageResponse(BaseModel):
    session: SessionMeta
    user_message: ChatMessage
    assistant_message: ChatMessage
    run_id: str
    mode: str
    warning: str | None = None


class ApprovalsResponse(BaseModel):
    items: list[ApprovalRecord] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    mode: str
