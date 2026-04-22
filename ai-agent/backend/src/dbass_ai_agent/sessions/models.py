from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SessionStatus = Literal["active", "archived", "deleted"]
MessageRole = Literal["user", "assistant", "system"]


class ChatMessage(BaseModel):
    message_id: str
    role: MessageRole
    content: str
    created_at: datetime


class SessionMeta(BaseModel):
    session_id: str
    user_id: str
    role: Literal["admin", "user"]
    user: str | None = None
    thread_id: str
    title: str
    status: SessionStatus = "active"
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None


class SessionIndexItem(BaseModel):
    session_id: str
    title: str
    status: SessionStatus
    updated_at: datetime
    last_message_at: datetime | None = None
    preview: str = ""


class SessionSummary(BaseModel):
    summary: str = ""


class ApprovalRecord(BaseModel):
    approval_id: str
    status: Literal["pending", "approved", "rejected"] = "pending"
    action: str = ""
    created_at: datetime


class SessionDetail(BaseModel):
    meta: SessionMeta
    messages: list[ChatMessage] = Field(default_factory=list)
    summary: SessionSummary = Field(default_factory=SessionSummary)
    approvals: list[ApprovalRecord] = Field(default_factory=list)


class SessionMessageResult(BaseModel):
    session: SessionMeta
    user_message: ChatMessage
    assistant_message: ChatMessage
    run_id: str
    mode: Literal["demo", "deepagent"]
    warning: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
