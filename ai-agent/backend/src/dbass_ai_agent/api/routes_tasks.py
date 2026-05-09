from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from dbass_ai_agent.agent.runtime import AgentInvocationError, DeepAgentRuntime
from dbass_ai_agent.config import Settings
from dbass_ai_agent.dbaas.task_status import is_terminal_task_status
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.operations.models import TaskRecord
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.run_lock import session_locks
from dbass_ai_agent.sessions.service import SessionService

from .deps import (
    get_agent_runtime,
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
    agent_runtime: DeepAgentRuntime = Depends(get_agent_runtime),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    session = session_service.get_session(identity, session_id).meta
    interval_seconds = max(1, settings.dbaas_task_refresh_interval_seconds)

    async def generate() -> AsyncIterator[str]:
        initial_tasks = await asyncio.to_thread(task_service.list_tasks, session)
        previous = {task.task_id: task for task in initial_tasks}
        pending_followup_task_ids: set[str] = set()
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
                previous_task = previous.get(task.task_id)
                if previous_task is None:
                    continue
                if _task_event_signature(previous_task) == _task_event_signature(task):
                    continue
                if (
                    not is_terminal_task_status(previous_task.status)
                    and is_terminal_task_status(task.status)
                ):
                    pending_followup_task_ids.add(task.task_id)
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

            async for event, payload in _run_pending_task_followup(
                identity=identity,
                session_service=session_service,
                task_service=task_service,
                agent_runtime=agent_runtime,
                session=session,
                latest_tasks=latest,
                pending_task_ids=pending_followup_task_ids,
            ):
                yield _sse_event(event, payload)

            previous = latest_by_id
            if (
                not any(not is_terminal_task_status(task.status) for task in latest)
                and not pending_followup_task_ids
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


async def _run_pending_task_followup(
    *,
    identity: Identity,
    session_service: SessionService,
    task_service: TaskService,
    agent_runtime: DeepAgentRuntime,
    session,
    latest_tasks: list[TaskRecord],
    pending_task_ids: set[str],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    if not pending_task_ids:
        return

    active_task_ids = {
        task.task_id
        for task in latest_tasks
        if is_terminal_task_status(task.status) and not task.agent_followup_triggered
    }
    pending_task_ids.intersection_update(active_task_ids)
    if not pending_task_ids:
        return

    with session_locks.acquire_run_lock(session.session_id) as acquired:
        if not acquired:
            return

        latest_by_id = {
            task.task_id: task
            for task in task_service.list_tasks(session)
            if is_terminal_task_status(task.status) and not task.agent_followup_triggered
        }
        tasks = [
            latest_by_id[task_id]
            for task_id in sorted(pending_task_ids)
            if task_id in latest_by_id
        ]
        pending_task_ids.difference_update(
            task_id for task_id in list(pending_task_ids) if task_id not in latest_by_id
        )
        if not tasks:
            return

        marked_tasks = task_service.mark_agent_followup_triggered(session, tasks)
        pending_task_ids.difference_update(task.task_id for task in marked_tasks)
        ai_agent_message = session_service.append_ai_agent_message(
            identity,
            session.session_id,
            _build_followup_notice(marked_tasks),
        )
        yield (
            "task_followup_started",
            {
                "session_id": session.session_id,
                "tasks": [_task_payload(task) for task in marked_tasks],
                "ai_agent_message": _message_payload(ai_agent_message),
            },
        )

        prompt = _build_followup_prompt(marked_tasks)
        try:
            reply = await asyncio.to_thread(
                agent_runtime.generate_followup,
                identity=identity,
                session=session,
                prompt=prompt,
            )
        except AgentInvocationError as exc:
            error_message = session_service.append_ai_agent_message(
                identity,
                session.session_id,
                f"AI Agent 自动查询任务执行结果失败：{exc}",
            )
            yield (
                "task_followup_failed",
                {
                    "session_id": session.session_id,
                    "tasks": [_task_payload(task) for task in marked_tasks],
                    "ai_agent_message": _message_payload(error_message),
                    "error": exc.to_payload(),
                },
            )
            return

        if reply.approval_request is not None:
            blocked_message = session_service.append_ai_agent_message(
                identity,
                session.session_id,
                "AI Agent 自动回访只允许查询任务结果，不会发起新的 DBAAS 写操作。",
            )
            yield (
                "task_followup_failed",
                {
                    "session_id": session.session_id,
                    "tasks": [_task_payload(task) for task in marked_tasks],
                    "ai_agent_message": _message_payload(blocked_message),
                    "error": {
                        "error_type": "followup_write_interrupted",
                        "detail": "自动回访触发了写操作审批，已中止。",
                        "stage": "followup",
                    },
                },
            )
            return

        assistant_message = session_service.append_assistant_message(
            identity,
            session.session_id,
            reply.content,
        )
        yield (
            "task_followup_completed",
            {
                "session_id": session.session_id,
                "tasks": [_task_payload(task) for task in marked_tasks],
                "assistant_message": _message_payload(assistant_message),
                "run_id": reply.run_id,
                "mode": reply.mode,
            },
        )


def _build_followup_notice(tasks: list[TaskRecord]) -> str:
    if len(tasks) == 1:
        task = tasks[0]
        return f"AI Agent 检测到异步任务 {task.task_id} 已{_task_status_text(task.status)}。"
    return f"AI Agent 检测到 {len(tasks)} 个异步任务已结束。"


def _build_followup_prompt(tasks: list[TaskRecord]) -> str:
    payload = [_task_payload(task) for task in tasks]
    return (
        "这是 AI Agent 自动触发的异步任务终态回访，不是用户新请求。\n"
        "请先使用 DBAAS 任务查询工具查询这些 task 的执行结果；"
        "已知 task_id 时优先使用 get_dbaas_task_tool。\n"
        "必要时可以使用 DBAAS 只读工具查询目标资源当前状态。\n"
        "只允许查询和总结，不要调用任何写工具，不要创建、更新、扩容、重启或升级资源。\n"
        "如果需要后续变更，只能给出建议，等待用户再次发起并走人工审批。\n"
        "请用中文简要总结执行结果、影响范围和后续建议。\n"
        f"任务列表 JSON：\n{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
    )


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
