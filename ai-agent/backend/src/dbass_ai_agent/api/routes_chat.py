from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from dbass_ai_agent.agent.runtime import AgentInvocationError, DeepAgentRuntime
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.sessions.service import SessionService

from .deps import get_agent_runtime, get_current_identity, get_session_service
from .schemas import SendMessageRequest, SendMessageResponse


router = APIRouter(prefix="/api/v1", tags=["chat"])
logger = logging.getLogger(__name__)


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
    assistant_message = session_service.append_assistant_message(
        identity,
        session_id,
        reply.content,
    )
    latest_session = session_service.get_session(identity, session_id).meta
    return SendMessageResponse(
        session=latest_session,
        user_message=user_message,
        assistant_message=assistant_message,
        run_id=reply.run_id,
        mode=reply.mode,
        warning=reply.warning,
    )


@router.post("/sessions/{session_id}/messages/stream")
def stream_message(
    session_id: str,
    payload: SendMessageRequest,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    agent_runtime: DeepAgentRuntime = Depends(get_agent_runtime),
) -> StreamingResponse:
    user_message = session_service.append_user_message(
        identity,
        session_id,
        payload.content,
    )
    session = session_service.get_session(identity, session_id).meta

    def event_stream() -> Iterator[str]:
        yield _sse_event("user_message", {"user_message": user_message})

        assistant_content = ""
        final_event = None
        try:
            for event in agent_runtime.stream_reply(session=session, user_message=user_message):
                if event.event == "started":
                    yield _sse_event(
                        "started",
                        {
                            "run_id": event.run_id,
                            "mode": event.mode,
                            "warning": event.warning,
                        },
                    )
                    continue

                if event.event == "token":
                    assistant_content += event.content
                    yield _sse_event(
                        "token",
                        {
                            "run_id": event.run_id,
                            "mode": event.mode,
                            "delta": event.content,
                            "warning": event.warning,
                        },
                    )
                    continue

                if event.event in {"compression_started", "compression_completed"}:
                    yield _sse_event(
                        event.event,
                        {
                            "run_id": event.run_id,
                            "mode": event.mode,
                            "message": event.content,
                            "details": event.details or {},
                        },
                    )
                    continue

                if event.event == "completed":
                    final_event = event
        except AgentInvocationError as exc:
            logger.exception("DeepAgent 流式调用失败 session_id=%s", session_id)
            yield _sse_event("error", exc.to_payload())
            return
        except Exception:
            logger.exception("DeepAgent 流式调用出现未分类异常 session_id=%s", session_id)
            yield _sse_event(
                "error",
                {
                    "detail": "流式响应失败，请查看后端日志。",
                    "error_type": "stream_error",
                    "stage": "stream",
                },
            )
            return

        if final_event is None:
            yield _sse_event(
                "error",
                {
                    "detail": "流式响应未正常结束。",
                    "error_type": "incomplete_stream",
                    "stage": "stream",
                },
            )
            return

        assistant_text = final_event.content or assistant_content
        assistant_message = session_service.append_assistant_message(
            identity,
            session_id,
            assistant_text,
        )
        latest_session = session_service.get_session(identity, session_id).meta
        yield _sse_event(
            "done",
            {
                "session": latest_session,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "run_id": final_event.run_id,
                "mode": final_event.mode,
                "warning": final_event.warning,
            },
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"
