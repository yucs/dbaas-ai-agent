from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status

from dbass_ai_agent.agent.factory import AgentFactoryError
from dbass_ai_agent.agent.runtime import DeepAgentRuntime
from dbass_ai_agent.config import Settings, get_settings
from dbass_ai_agent.dbaas.config import dbaas_config_from_settings
from dbass_ai_agent.dbaas.write_client import DbaasWriteClient
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.identity.resolver import resolve_identity
from dbass_ai_agent.operations.approval_service import ApprovalService
from dbass_ai_agent.operations.operation_service import OperationService
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.approval_store import ApprovalStore
from dbass_ai_agent.sessions.index_store import IndexStore
from dbass_ai_agent.sessions.message_store import MessageStore
from dbass_ai_agent.sessions.operation_store import OperationStore
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.service import SessionService
from dbass_ai_agent.sessions.task_store import TaskStore
from dbass_ai_agent.sessions.thread_binding import ThreadBinding


def get_current_identity(request: Request) -> Identity:
    return resolve_identity(request)


@lru_cache
def get_session_repository() -> SessionRepository:
    settings = get_settings()
    return SessionRepository(
        data_root=settings.data_root,
        index_store=IndexStore(),
        message_store=MessageStore(),
        approval_store=ApprovalStore(),
        operation_store=OperationStore(),
        task_store=TaskStore(),
    )


@lru_cache
def get_session_service() -> SessionService:
    return SessionService(
        repository=get_session_repository(),
        thread_binding=ThreadBinding(),
    )


def get_operation_service(
    session_service: SessionService = Depends(get_session_service),
) -> OperationService:
    return OperationService(repository=session_service.repository)


def get_task_service(
    session_service: SessionService = Depends(get_session_service),
) -> TaskService:
    settings = get_settings()
    return TaskService(
        repository=session_service.repository,
        dbaas_config=dbaas_config_from_settings(settings),
    )


def get_approval_service(
    session_service: SessionService = Depends(get_session_service),
) -> ApprovalService:
    settings = get_settings()
    dbaas_config = dbaas_config_from_settings(settings)
    current_value_client = DbaasWriteClient(dbaas_config) if settings.dbaas_approval_current_value_enabled else None
    return ApprovalService(
        repository=session_service.repository,
        session_service=session_service,
        current_value_client=current_value_client,
        current_value_timeout_seconds=settings.dbaas_approval_current_value_timeout_seconds,
    )


@lru_cache
def _get_cached_operation_service() -> OperationService:
    return OperationService(repository=get_session_repository())


@lru_cache
def _get_cached_task_service() -> TaskService:
    settings = get_settings()
    return TaskService(
        repository=get_session_repository(),
        dbaas_config=dbaas_config_from_settings(settings),
    )


@lru_cache
def get_agent_runtime() -> DeepAgentRuntime:
    try:
        return DeepAgentRuntime(
            get_settings(),
            operation_service=_get_cached_operation_service(),
            task_service=_get_cached_task_service(),
        )
    except AgentFactoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def get_agent_runtime_factory(request: Request) -> Callable[[], DeepAgentRuntime]:
    override = request.app.dependency_overrides.get(get_agent_runtime)
    if override is not None:
        return override
    return get_agent_runtime


def get_app_settings() -> Settings:
    return get_settings()


async def close_agent_runtime() -> None:
    if get_agent_runtime.cache_info().currsize == 0:
        return

    runtime = get_agent_runtime()
    await runtime.aclose()
    get_agent_runtime.cache_clear()
