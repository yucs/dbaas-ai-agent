from __future__ import annotations

import json
from typing import Any

from dbass_ai_agent.dbaas.config import DbaasConfig
from dbass_ai_agent.dbaas.task_status import is_terminal_task_status, map_dbaas_task_status
from dbass_ai_agent.dbaas.write_client import DbaasWriteClient, DbaasWriteClientError
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.clock import utc_now
from dbass_ai_agent.operations.models import (
    OperationError,
    OperationResult,
    OperationStatus,
    OperationTarget,
    OperationTaskRef,
    TaskRecord,
)
from dbass_ai_agent.sessions.models import SessionMeta
from dbass_ai_agent.sessions.repository import SessionRepository


class TaskConflictError(RuntimeError):
    def __init__(self, task: TaskRecord) -> None:
        super().__init__("当前 Session 已存在同类未结束任务。")
        self.task = task


class TaskService:
    def __init__(
        self,
        repository: SessionRepository,
        dbaas_config: DbaasConfig,
        write_client: DbaasWriteClient | None = None,
    ) -> None:
        self.repository = repository
        self.dbaas_config = dbaas_config
        self.write_client = write_client or DbaasWriteClient(dbaas_config)

    def list_tasks(self, session: SessionMeta) -> list[TaskRecord]:
        return self.repository.load_tasks(session.user_id, session.session_id)

    def list_tasks_with_lazy_refresh(
        self,
        identity: Identity,
        session: SessionMeta,
    ) -> list[TaskRecord]:
        tasks = self.list_tasks(session)
        refreshed: dict[str, TaskRecord] = {}
        for task in tasks:
            if is_terminal_task_status(task.status):
                refreshed[task.task_id] = task
                continue
            refreshed[task.task_id] = self.refresh_task(identity, session, task)
        return list(refreshed.values())

    def ensure_no_conflicting_task(
        self,
        session: SessionMeta,
        conflict_key: str,
    ) -> None:
        for task in self.list_tasks(session):
            if task.operation_conflict_key == conflict_key and not is_terminal_task_status(task.status):
                raise TaskConflictError(task)

    def create_task_record(
        self,
        session: SessionMeta,
        *,
        task_id: str,
        operation_id: str,
        action: str,
        targets: list[OperationTarget],
        dbaas_type: str,
        source_status: str | None = "RUNNING",
        message: str | None = None,
        reason: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> TaskRecord:
        now = utc_now()
        task = TaskRecord(
            task_id=task_id,
            operation_id=operation_id,
            session_id=session.session_id,
            action=action,
            operation_conflict_key=build_operation_conflict_key(action, targets),
            targets=targets,
            dbaas_type=dbaas_type,
            status=map_dbaas_task_status(source_status),
            source_status=source_status,
            message=message,
            reason=reason,
            result=result,
            last_error=None,
            created_at=now,
            updated_at=now,
            last_checked_at=now,
        )
        self.repository.append_task(session.user_id, session.session_id, task)
        return task

    def refresh_task(
        self,
        identity: Identity,
        session: SessionMeta,
        task: TaskRecord,
    ) -> TaskRecord:
        if is_terminal_task_status(task.status):
            self._sync_terminal_operation_status(session, task)
            return task

        checked_at = utc_now()
        try:
            payload = self.write_client.get_task(
                identity,
                task.task_id,
                timeout_seconds=self.dbaas_config.request_timeout_seconds,
            )
        except DbaasWriteClientError as exc:
            candidate = task.model_copy(
                update={
                    "status": "refresh_failed",
                    "last_error": str(exc),
                    "last_checked_at": checked_at,
                    "updated_at": checked_at,
                }
            )
            return self._append_if_visible_changed(session, task, candidate)

        source_status = _string(payload.get("status"))
        candidate = task.model_copy(
            update={
                "status": map_dbaas_task_status(source_status),
                "source_status": source_status,
                "message": _string(payload.get("message")),
                "reason": _string(payload.get("reason")),
                "result": payload.get("result") if isinstance(payload.get("result"), dict) else None,
                "last_error": None,
                "updated_at": _parse_time_or_now(payload.get("updatedAt")),
                "last_checked_at": checked_at,
            }
        )
        return self._append_if_visible_changed(session, task, candidate)

    def _append_if_visible_changed(
        self,
        session: SessionMeta,
        previous: TaskRecord,
        candidate: TaskRecord,
    ) -> TaskRecord:
        if _task_visible_signature(previous) == _task_visible_signature(candidate):
            return previous
        self.repository.append_task(session.user_id, session.session_id, candidate)
        if is_terminal_task_status(candidate.status):
            self._sync_terminal_operation_status(session, candidate)
        return candidate

    def _sync_terminal_operation_status(
        self,
        session: SessionMeta,
        task: TaskRecord,
    ) -> None:
        operation_status = _operation_status_from_task(task)
        if operation_status is None:
            return
        operation = next(
            (
                item
                for item in self.repository.load_operations(session.user_id, session.session_id)
                if item.operation_id == task.operation_id
            ),
            None,
        )
        if operation is None:
            return
        existing_result = operation.result
        if operation.status == operation_status and existing_result and existing_result.status == operation_status:
            return
        if existing_result is not None:
            details = dict(existing_result.details)
            details["task"] = task.model_dump(mode="json")
            task_ref = existing_result.task or OperationTaskRef(
                task_id=task.task_id,
                type=task.dbaas_type,
                status=task.status,
            )
            result = existing_result.model_copy(
                update={
                    "status": operation_status,
                    "summary": f"异步任务 {task.task_id} 已{_operation_status_text(operation_status)}。",
                    "task": task_ref.model_copy(update={"status": task.status}),
                    "details": details,
                    "error": _task_error(task) if operation_status in {"failed", "canceled"} else None,
                }
            )
        else:
            result = OperationResult(
                operation_id=operation.operation_id,
                approval_id=operation.approval_id,
                action=operation.action,
                targets=task.targets,
                execution_mode=operation.execution_mode,
                status=operation_status,
                summary=f"异步任务 {task.task_id} 已{_operation_status_text(operation_status)}。",
                task=OperationTaskRef(
                    task_id=task.task_id,
                    type=task.dbaas_type,
                    status=task.status,
                ),
                error=_task_error(task) if operation_status in {"failed", "canceled"} else None,
                details={"task": task.model_dump(mode="json")},
            )
        updated = operation.model_copy(
            update={
                "status": operation_status,
                "result": result,
                "completed_at": task.updated_at or utc_now(),
            }
        )
        self.repository.append_operation(session.user_id, session.session_id, updated)


def build_operation_conflict_key(action: str, targets: list[OperationTarget]) -> str:
    parts: list[str] = []
    for target in targets:
        qualifier_items = sorted((str(key), str(value)) for key, value in target.qualifiers.items())
        qualifier_text = ",".join(f"{key}={value}" for key, value in qualifier_items)
        parts.append(f"{target.kind}:{target.id}:{qualifier_text}")
    return f"{action}|{'|'.join(sorted(parts))}"


def _operation_status_from_task(task: TaskRecord) -> OperationStatus | None:
    if task.status == "succeeded":
        return "succeeded"
    if task.status == "failed":
        return "failed"
    if task.status == "canceled":
        return "canceled"
    return None


def _task_error(task: TaskRecord) -> OperationError:
    return OperationError(
        error_type=f"task_{task.status}",
        message=task.reason or task.last_error or task.message or f"任务 {task.task_id} {task.status}",
    )


def _operation_status_text(status: OperationStatus) -> str:
    mapping = {
        "succeeded": "成功",
        "failed": "失败",
        "canceled": "取消",
    }
    return mapping.get(status, status)


def _task_visible_signature(task: TaskRecord) -> str:
    payload = {
        "status": task.status,
        "source_status": task.source_status,
        "message": task.message,
        "reason": task.reason,
        "result": task.result,
        "last_error": task.last_error,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _parse_time_or_now(value: Any):
    if isinstance(value, str):
        from datetime import datetime

        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return utc_now()
