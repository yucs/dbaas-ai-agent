from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from dbass_ai_agent.agent.factory import AgentFactoryError, delete_thread_checkpoint
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.logging import log_context
from dbass_ai_agent.dbaas.task_status import is_terminal_task_status
from dbass_ai_agent.operations.approval_service import ApprovalService
from dbass_ai_agent.operations.operation_service import OperationService
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.run_lock import session_locks
from dbass_ai_agent.sessions.service import SessionService

from .deps import (
    get_app_settings,
    get_agent_runtime,
    get_approval_service,
    get_current_identity,
    get_operation_service,
    get_session_service,
    get_task_service,
)
from .schemas import (
    CreateSessionRequest,
    DeleteSessionResponse,
    SessionListResponse,
    SessionMetaResponse,
    SessionResponse,
)


router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


@router.get("", response_model=SessionListResponse)
def list_sessions(
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> SessionListResponse:
    return SessionListResponse(items=session_service.list_sessions(identity))


@router.post("", response_model=SessionResponse)
def create_session(
    payload: CreateSessionRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    detail = session_service.create_session(
        identity,
        title=payload.title,
    )
    with log_context(
        request_id=getattr(request.state, "request_id", "-"),
        user_id=identity.user_id,
        role=identity.role,
        session_id=detail.meta.session_id,
        thread_id=detail.meta.thread_id,
    ):
        logger.info("session created title=%s", detail.meta.title)
    return SessionResponse(session=detail)


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime=Depends(get_agent_runtime),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
) -> SessionResponse:
    approval_service.expire_pending_approvals(
        identity,
        session_id,
        agent_runtime=agent_runtime,
        operation_service=operation_service,
        task_service=task_service,
    )
    return SessionResponse(session=session_service.get_session(identity, session_id))


@router.post("/{session_id}/archive", response_model=SessionMetaResponse)
def archive_session(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime=Depends(get_agent_runtime),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
) -> SessionMetaResponse:
    _assert_no_unfinished_items(
        identity,
        session_id,
        session_service,
        approval_service,
        task_service,
        agent_runtime=agent_runtime,
        operation_service=operation_service,
        allow_failed_expired_resume=False,
    )
    return SessionMetaResponse(session=session_service.archive_session(identity, session_id).meta)


@router.post("/{session_id}/restore", response_model=SessionMetaResponse)
def restore_session(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> SessionMetaResponse:
    return SessionMetaResponse(session=session_service.restore_session(identity, session_id).meta)


@router.delete("/{session_id}", response_model=DeleteSessionResponse)
def delete_session(
    session_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime=Depends(get_agent_runtime),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
    settings=Depends(get_app_settings),
) -> DeleteSessionResponse:
    _assert_no_unfinished_items(
        identity,
        session_id,
        session_service,
        approval_service,
        task_service,
        agent_runtime=agent_runtime,
        operation_service=operation_service,
        allow_failed_expired_resume=True,
    )
    detail = session_service.get_session(identity, session_id)
    with log_context(
        request_id=getattr(request.state, "request_id", "-"),
        user_id=identity.user_id,
        role=identity.role,
        session_id=session_id,
        thread_id=detail.meta.thread_id,
    ):
        logger.debug("session delete started")
        try:
            delete_thread_checkpoint(settings, detail.meta.thread_id)
        except AgentFactoryError as exc:
            logger.exception("session checkpoint delete failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        deleted_session_id = session_service.delete_session(identity, session_id)
        logger.info("session deleted")
        return DeleteSessionResponse(session_id=deleted_session_id)


def _assert_no_unfinished_items(
    identity: Identity,
    session_id: str,
    session_service: SessionService,
    approval_service: ApprovalService,
    task_service: TaskService,
    *,
    agent_runtime,
    operation_service: OperationService,
    allow_failed_expired_resume: bool,
) -> None:
    approval_service.expire_pending_approvals(
        identity,
        session_id,
        agent_runtime=agent_runtime,
        operation_service=operation_service,
        task_service=task_service,
    )
    detail = session_service.get_session(identity, session_id)
    if not allow_failed_expired_resume and approval_service.has_failed_expired_resume(detail.meta):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "expired_approval_resume_failed",
                "detail": "当前 Session 的过期审批暂停点尚未清理完成，请稍后重试。",
            },
        )
    if session_locks.is_run_locked(detail.meta.session_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "session_run_locked",
                "detail": "当前 Session 正在执行 AI 请求，请等待本轮完成后再归档或删除。",
            },
        )
    if any(approval.status == "pending" for approval in detail.approvals):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "session_has_pending_approval",
                "detail": "当前 Session 存在待确认操作，请先批准或拒绝后再归档或删除。",
            },
        )
    if any(not is_terminal_task_status(task.status) for task in task_service.list_tasks(detail.meta)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "session_has_running_tasks",
                "detail": "当前 Session 存在运行中的 DBAAS 任务，请等待任务结束后再归档或删除。",
            },
        )
