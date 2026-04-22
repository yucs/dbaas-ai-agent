from __future__ import annotations

from fastapi import APIRouter, Depends

from dbass_ai_agent.agent.runtime import DemoAgentRuntime
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
    agent_runtime: DemoAgentRuntime = Depends(get_agent_runtime),
) -> SendMessageResponse:
    user_message = session_service.append_user_message(identity, session_id, payload.content)
    session = session_service.get_session(identity, session_id).meta
    history = session_service.get_messages(identity, session_id)
    reply = agent_runtime.generate_reply(
        identity=identity,
        session=session,
        user_message=user_message,
        history=history,
    )
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
