from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from dbass_ai_agent.agent.runtime import AgentInvocationError, DeepAgentRuntime
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.sessions.service import SessionService

from .deps import get_agent_runtime, get_current_identity, get_session_service
from .schemas import SendMessageRequest, SendMessageResponse


router = APIRouter(prefix="/api/v1", tags=["chat"])


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def send_message(
    session_id: str,
    payload: SendMessageRequest,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    agent_runtime: DeepAgentRuntime = Depends(get_agent_runtime),
) -> SendMessageResponse:
    user_message = session_service.append_user_message(identity, session_id, payload.content)
    session = session_service.get_session(identity, session_id).meta
    try:
        reply = agent_runtime.generate_reply(
            session=session,
            user_message=user_message,
        )
    except AgentInvocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    assistant_message = session_service.append_assistant_message(identity, session_id, reply.content)
    latest_session = session_service.get_session(identity, session_id).meta
    return SendMessageResponse(
        session=latest_session,
        user_message=user_message,
        assistant_message=assistant_message,
        run_id=reply.run_id,
        mode=reply.mode,
        warning=reply.warning,
    )
