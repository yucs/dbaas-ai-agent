from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RiskLevel = Literal["low", "medium", "high", "critical"]
RequiredRole = Literal["user", "admin"]
ExecutionMode = Literal["sync", "async"]
ProposalExecutionMode = Literal["sync", "async", "mixed"]
OperationStatus = Literal[
    "started",
    "succeeded",
    "failed",
    "timeout",
    "unknown",
    "task_created",
    "canceled",
]
TaskStatus = Literal["running", "succeeded", "failed", "canceled", "unknown", "refresh_failed"]


class OperationTarget(BaseModel):
    kind: str
    id: str
    name: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)


class OperationParameter(BaseModel):
    key: str
    label: str
    value: Any
    unit: str | None = None
    current_value: Any | None = None
    current_unit: str | None = None


class OperationProposalItem(BaseModel):
    action: str
    targets: list[OperationTarget] = Field(default_factory=list)
    summary: str
    risk_level: RiskLevel = "medium"
    required_role: RequiredRole = "user"
    execution_mode: ExecutionMode
    parameters: list[OperationParameter] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class OperationProposal(BaseModel):
    summary: str
    risk_level: RiskLevel = "medium"
    required_role: RequiredRole = "user"
    execution_mode: ProposalExecutionMode
    items: list[OperationProposalItem] = Field(default_factory=list)


class InterruptedToolCall(BaseModel):
    tool_call_id: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)


class OperationChange(BaseModel):
    target: OperationTarget
    field: str
    label: str
    before: Any | None = None
    after: Any | None = None
    unit: str | None = None
    change_type: str | None = None


class OperationError(BaseModel):
    error_type: str
    message: str


class OperationTaskRef(BaseModel):
    task_id: str
    type: str
    status: str


class OperationResult(BaseModel):
    operation_id: str
    approval_id: str | None = None
    action: str
    targets: list[OperationTarget] = Field(default_factory=list)
    execution_mode: ExecutionMode
    status: OperationStatus
    summary: str
    task: OperationTaskRef | None = None
    changes: list[OperationChange] = Field(default_factory=list)
    error: OperationError | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class OperationRecord(BaseModel):
    operation_id: str
    approval_id: str | None = None
    session_id: str
    thread_id: str
    run_id: str | None = None
    tool_call_id: str | None = None
    action: str
    execution_mode: ExecutionMode
    status: OperationStatus
    result: OperationResult | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskRecord(BaseModel):
    task_id: str
    operation_id: str
    session_id: str
    action: str
    operation_conflict_key: str
    targets: list[OperationTarget] = Field(default_factory=list)
    dbaas_type: str
    status: TaskStatus
    source_status: str | None = None
    message: str | None = None
    reason: str | None = None
    result: dict[str, Any] | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    last_checked_at: datetime | None = None
