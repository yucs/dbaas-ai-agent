from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from dbass_ai_agent.config import Settings
from dbass_ai_agent.dbaas.task_status import is_terminal_task_status
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.operations.models import TaskRecord
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.service import SessionService

from .deps import get_app_settings, get_current_identity, get_session_service, get_task_service
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

            previous = latest_by_id
            if not any(not is_terminal_task_status(task.status) for task in latest):
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


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
