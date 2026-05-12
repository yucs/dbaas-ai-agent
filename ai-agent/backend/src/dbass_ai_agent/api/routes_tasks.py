from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from dbass_ai_agent.config import Settings
from dbass_ai_agent.dbaas.task_status import is_terminal_task_status
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.operations.models import OperationRecord, TaskRecord
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.run_lock import session_locks
from dbass_ai_agent.sessions.service import SessionService

from .deps import (
    get_app_settings,
    get_current_identity,
    get_session_service,
    get_task_service,
)
from .schemas import TasksResponse


router = APIRouter(prefix="/api/v1/sessions", tags=["tasks"])


@router.get("/{session_id}/tasks", response_model=TasksResponse)
def get_session_tasks(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    task_service: TaskService = Depends(get_task_service),
) -> TasksResponse:
    session = session_service.get_session(identity, session_id).meta
    return TasksResponse(items=task_service.list_tasks_with_lazy_refresh(identity, session))


@router.get("/{session_id}/tasks/events")
async def stream_session_task_events(
    session_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    task_service: TaskService = Depends(get_task_service),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    session = session_service.get_session(identity, session_id).meta
    interval_seconds = max(1, settings.dbaas_task_refresh_interval_seconds)

    async def generate() -> AsyncIterator[str]:
        initial_tasks = await asyncio.to_thread(task_service.list_tasks, session)
        previous = {task.task_id: task for task in initial_tasks}
        pending_terminal_task_ids: set[str] = {
            task.task_id
            for task in initial_tasks
            if is_terminal_task_status(task.status) and not task.terminal_notice_emitted
        }
        while True:
            if await request.is_disconnected():
                break

            latest = await asyncio.to_thread(
                task_service.list_tasks_with_lazy_refresh,
                identity,
                session,
            )
            latest_by_id = {task.task_id: task for task in latest}
            for task in latest:
                if is_terminal_task_status(task.status) and not task.terminal_notice_emitted:
                    pending_terminal_task_ids.add(task.task_id)
                previous_task = previous.get(task.task_id)
                if previous_task is None:
                    continue
                if _task_event_signature(previous_task) == _task_event_signature(task):
                    continue
                if (
                    not is_terminal_task_status(previous_task.status)
                    and is_terminal_task_status(task.status)
                ):
                    pending_terminal_task_ids.add(task.task_id)
                yield _sse_event(
                    "task_status_changed",
                    {
                        "session_id": session.session_id,
                        "task": {
                            **task.model_dump(mode="json"),
                            "previous_status": previous_task.status,
                        },
                    },
                )

            async for event, payload in _emit_pending_terminal_notices(
                identity=identity,
                session_service=session_service,
                task_service=task_service,
                session=session,
                pending_task_ids=pending_terminal_task_ids,
            ):
                yield _sse_event(event, payload)

            previous = latest_by_id
            if (
                not any(not is_terminal_task_status(task.status) for task in latest)
                and not pending_terminal_task_ids
            ):
                break
            await asyncio.sleep(interval_seconds)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _task_event_signature(task: TaskRecord) -> tuple[object, ...]:
    return (
        task.status,
        task.source_status,
        task.message,
        task.reason,
        json.dumps(task.result, ensure_ascii=False, sort_keys=True) if task.result is not None else None,
        task.last_error,
    )


async def _emit_pending_terminal_notices(
    *,
    identity: Identity,
    session_service: SessionService,
    task_service: TaskService,
    session,
    pending_task_ids: set[str],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    if not pending_task_ids:
        return

    with session_locks.acquire_run_lock(session.session_id) as acquired:
        if not acquired:
            return

        latest_tasks = task_service.list_tasks(session)
        operations_by_id = {
            operation.operation_id: operation
            for operation in task_service.repository.load_operations(session.user_id, session.session_id)
        }
        terminal_unnotified_ids = {
            task.task_id
            for task in latest_tasks
            if is_terminal_task_status(task.status) and not task.terminal_notice_emitted
        }
        pending_task_ids.intersection_update(terminal_unnotified_ids)
        if not pending_task_ids:
            return

        groups: dict[str, tuple[str, list[TaskRecord]]] = {}
        tasks_by_id = {task.task_id: task for task in latest_tasks}
        for task_id in sorted(pending_task_ids):
            task = tasks_by_id.get(task_id)
            if task is None:
                pending_task_ids.discard(task_id)
                continue
            group_key, subject = _task_notice_group(task, operations_by_id)
            groups.setdefault(group_key, (subject, []))[1].append(task)

        for group_key, (subject, pending_tasks) in groups.items():
            group_tasks = [
                task
                for task in latest_tasks
                if _task_notice_group(task, operations_by_id)[0] == group_key
            ]
            if not group_tasks:
                pending_task_ids.difference_update(task.task_id for task in pending_tasks)
                continue
            if not all(is_terminal_task_status(task.status) for task in group_tasks):
                continue
            if not any(not task.terminal_notice_emitted for task in group_tasks):
                pending_task_ids.difference_update(task.task_id for task in group_tasks)
                continue

            marked_tasks = task_service.mark_terminal_notice_emitted(session, group_tasks)
            pending_task_ids.difference_update(task.task_id for task in marked_tasks)
            system_message = session_service.append_system_message(
                identity,
                session.session_id,
                _build_terminal_notice(subject, marked_tasks),
                restore_archived=False,
            )
            yield (
                "task_terminal_notice_emitted",
                {
                    "session_id": session.session_id,
                    "group_key": group_key,
                    "tasks": [_task_payload(task) for task in marked_tasks],
                    "system_message": _message_payload(system_message) if system_message else None,
                },
            )


def _task_notice_group(
    task: TaskRecord,
    operations_by_id: dict[str, OperationRecord],
) -> tuple[str, str]:
    operation = operations_by_id.get(task.operation_id)
    if operation and operation.approval_id:
        return f"approval:{operation.approval_id}", "本次审批确认关联的异步任务"
    if task.operation_id:
        return f"operation:{task.operation_id}", "当前异步操作关联的异步任务"
    return f"task:{task.task_id}", "当前异步任务"


def _build_terminal_notice(subject: str, tasks: list[TaskRecord]) -> str:
    if len(tasks) == 1:
        task = tasks[0]
        content = f"{subject} {task.task_id} 已{_task_status_text(task.status)}。"
    else:
        content = f"{subject}已全部结束：{_task_status_counts_text(tasks)}。"
    if any(task.status in {"failed", "canceled"} for task in tasks):
        content += "如需进一步分析失败原因或处理建议，可以继续在本会话中提问。"
    return content


def _task_status_counts_text(tasks: list[TaskRecord]) -> str:
    labels = [
        ("succeeded", "成功"),
        ("failed", "失败"),
        ("canceled", "取消"),
    ]
    parts = [
        f"{sum(1 for task in tasks if task.status == status)} 个{label}"
        for status, label in labels
        if any(task.status == status for task in tasks)
    ]
    return "，".join(parts)


def _task_payload(task: TaskRecord) -> dict[str, Any]:
    return task.model_dump(mode="json")


def _message_payload(message) -> dict[str, Any]:
    return message.model_dump(mode="json")


def _task_status_text(status: str) -> str:
    mapping = {
        "succeeded": "成功",
        "failed": "失败",
        "canceled": "取消",
    }
    return mapping.get(status, status)


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
