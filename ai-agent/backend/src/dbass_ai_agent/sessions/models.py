from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from dbass_ai_agent.operations.models import (
    InterruptedToolCall,
    OperationProposal,
    OperationRecord,
)


SessionStatus = Literal["active", "archived", "deleted"]
MessageRole = Literal["user", "assistant", "system", "ai-agent"]


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


class ApprovalRecord(BaseModel):
    approval_id: str
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    action: str = ""
    session_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    request_message_id: str | None = None
    proposal: OperationProposal | None = None
    interrupted_tool_calls: list[InterruptedToolCall] = Field(default_factory=list)
    allowed_decisions: list[Literal["approve", "reject"]] = Field(default_factory=list)
    decided_by: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    expired_at: datetime | None = None
    resume_failed: bool = False
    resume_error: str | None = None
    resume_last_attempt_at: datetime | None = None


class SessionDetail(BaseModel):
    meta: SessionMeta
    messages: list[ChatMessage] = Field(default_factory=list)
    approvals: list[ApprovalRecord] = Field(default_factory=list)
    operations: list[OperationRecord] = Field(default_factory=list)
