from __future__ import annotations

from fastapi import APIRouter, Depends

from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.ids import new_thread_id
from dbass_ai_agent.sessions.service import SessionService

from .deps import get_current_identity, get_session_service
from .schemas import (
    ApprovalsResponse,
    CreateSessionRequest,
    DeleteSessionResponse,
    SessionListResponse,
    SessionMetaResponse,
    SessionResponse,
)


router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> SessionListResponse:
    return SessionListResponse(items=session_service.list_sessions(identity))


@router.post("", response_model=SessionResponse)
def create_session(
    payload: CreateSessionRequest,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    return SessionResponse(
        session=session_service.create_session(
            identity,
            title=payload.title,
            thread_id=new_thread_id(),
        )
    )


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
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
) -> DeleteSessionResponse:
    deleted_session_id = session_service.delete_session(identity, session_id)
    return DeleteSessionResponse(session_id=deleted_session_id)
