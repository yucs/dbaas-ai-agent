from __future__ import annotations

from dbass_ai_agent.operations.models import TaskStatus


TERMINAL_TASK_STATUSES: set[TaskStatus] = {"succeeded", "failed", "canceled"}
NON_TERMINAL_TASK_STATUSES: set[TaskStatus] = {"running", "unknown", "refresh_failed"}


def map_dbaas_task_status(source_status: str | None) -> TaskStatus:
    normalized = (source_status or "").strip().upper()
    if normalized in {"RUNNING", "PENDING", "CREATED", "STARTED"}:
        return "running"
    if normalized in {"SUCCESS", "SUCCEEDED", "COMPLETED", "DONE"}:
        return "succeeded"
    if normalized in {"FAILED", "FAILURE", "ERROR"}:
        return "failed"
    if normalized in {"CANCELED", "CANCELLED"}:
        return "canceled"
    return "unknown"


def is_terminal_task_status(status: TaskStatus) -> bool:
    return status in TERMINAL_TASK_STATUSES
