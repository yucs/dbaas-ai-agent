from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from dbass_ai_agent.agent.factory import AgentFactoryError, delete_thread_checkpoint
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.logging import log_context
from dbass_ai_agent.sessions.service import SessionService

from .deps import get_app_settings, get_current_identity, get_session_service
from .schemas import (
    ApprovalsResponse,
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
) -> SessionResponse:
    return SessionResponse(session=session_service.get_session(identity, session_id))


@router.get("/{session_id}/approvals", response_model=ApprovalsResponse)
def get_approvals(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> ApprovalsResponse:
    detail = session_service.get_session(identity, session_id)
    return ApprovalsResponse(items=detail.approvals)


@router.post("/{session_id}/archive", response_model=SessionMetaResponse)
def archive_session(
    session_id: str,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> SessionMetaResponse:
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
    settings=Depends(get_app_settings),
) -> DeleteSessionResponse:
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
