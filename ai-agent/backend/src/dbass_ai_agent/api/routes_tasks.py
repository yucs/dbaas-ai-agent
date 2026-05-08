from __future__ import annotations

from fastapi import APIRouter, Depends

from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.service import SessionService

from .deps import get_current_identity, get_session_service, get_task_service
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
