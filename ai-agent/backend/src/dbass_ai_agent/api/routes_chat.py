from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from dbass_ai_agent.agent.runtime import AgentInvocationError, DeepAgentRuntime
from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.logging import log_context
from dbass_ai_agent.operations.approval_service import ApprovalService, approval_interrupt_from_runtime
from dbass_ai_agent.operations.operation_service import OperationService
from dbass_ai_agent.operations.task_service import TaskService
from dbass_ai_agent.sessions.service import SessionService
from dbass_ai_agent.sessions.run_lock import session_locks

from .deps import (
    get_agent_runtime,
    get_app_settings,
    get_approval_service,
    get_current_identity,
    get_operation_service,
    get_session_service,
    get_task_service,
    renew_dbaas_user_lease,
)
from .schemas import SendMessageRequest, SendMessageResponse


router = APIRouter(prefix="/api/v1", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/sessions/{session_id}/messages", response_model=SendMessageResponse)
def send_message(
    session_id: str,
    payload: SendMessageRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime: DeepAgentRuntime = Depends(get_agent_runtime),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
    settings: Settings = Depends(get_app_settings),
) -> SendMessageResponse:
    content = _validate_message_content(payload.content, settings)
    request_id = getattr(request.state, "request_id", "-")
    with session_locks.acquire_run_lock(session_id) as acquired:
        if not acquired:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_type": "session_run_locked",
                    "detail": "当前 Session 正在执行 AI 请求，请等待本轮完成后再发送消息。",
                },
            )
        approval_service.expire_pending_approvals(
            identity,
            session_id,
            agent_runtime=agent_runtime,
            operation_service=operation_service,
            task_service=task_service,
            run_lock_already_held=True,
        )
        _assert_no_pending_approval(identity, session_id, approval_service)
        user_message = session_service.append_user_message(identity, session_id, content)
        session = session_service.get_session(identity, session_id).meta
        renew_dbaas_user_lease(request, identity)
        with log_context(
            request_id=request_id,
            user_id=identity.user_id,
            role=identity.role,
            session_id=session_id,
            thread_id=session.thread_id,
        ):
            logger.debug(
                "chat message accepted message_id=%s message_chars=%s",
                user_message.message_id,
                len(content),
            )
        try:
            reply = agent_runtime.generate_reply(
                identity=identity,
                session=session,
                user_message=user_message,
            )
        except AgentInvocationError as exc:
            logger.exception("DeepAgent 调用失败")
            ai_agent_message = session_service.append_ai_agent_message(
                identity,
                session_id,
                _build_ai_agent_error_content(exc.to_payload(), request_id=request_id),
            )
            logger.debug(
                "ai-agent error message persisted message_id=%s",
                ai_agent_message.message_id,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        if reply.approval_request is not None:
            approval = approval_service.create_approval(
                identity,
                session,
                run_id=reply.run_id,
                request_message_id=user_message.message_id,
                interrupt=approval_interrupt_from_runtime(reply.approval_request),
            )
            latest_session = session_service.get_session(identity, session_id).meta
            return SendMessageResponse(
                session=latest_session,
                user_message=user_message,
                assistant_message=None,
                run_id=reply.run_id,
                mode=reply.mode,
                warning=reply.warning,
                approval=approval,
                paused=True,
            )
        assistant_message = session_service.append_assistant_message(
            identity,
            session_id,
            reply.content,
        )
        logger.debug(
            "chat message completed assistant_message_id=%s response_chars=%s",
            assistant_message.message_id,
            len(reply.content),
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
    request: Request,
    identity: Identity = Depends(get_current_identity),
    session_service: SessionService = Depends(get_session_service),
    approval_service: ApprovalService = Depends(get_approval_service),
    agent_runtime: DeepAgentRuntime = Depends(get_agent_runtime),
    operation_service: OperationService = Depends(get_operation_service),
    task_service: TaskService = Depends(get_task_service),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    content = _validate_message_content(payload.content, settings)
    run_lock_context = session_locks.acquire_run_lock(session_id)
    acquired = run_lock_context.__enter__()
    if not acquired:
        run_lock_context.__exit__(None, None, None)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "session_run_locked",
                "detail": "当前 Session 正在执行 AI 请求，请等待本轮完成后再发送消息。",
            },
        )
    try:
        approval_service.expire_pending_approvals(
            identity,
            session_id,
            agent_runtime=agent_runtime,
            operation_service=operation_service,
            task_service=task_service,
            run_lock_already_held=True,
        )
        _assert_no_pending_approval(identity, session_id, approval_service)
        user_message = session_service.append_user_message(
            identity,
            session_id,
            content,
        )
        session = session_service.get_session(identity, session_id).meta
        renew_dbaas_user_lease(request, identity)
        request_id = getattr(request.state, "request_id", "-")
    except Exception:
        run_lock_context.__exit__(None, None, None)
        raise

    def event_stream() -> Iterator[str]:
        try:
            with log_context(
                request_id=request_id,
                user_id=identity.user_id,
                role=identity.role,
                session_id=session_id,
                thread_id=session.thread_id,
            ):
                logger.debug(
                    "chat stream accepted message_id=%s message_chars=%s",
                    user_message.message_id,
                    len(content),
                )
                yield _sse_event("user_message", {"user_message": user_message})

                assistant_content = ""
                final_event = None
                current_run_id = "-"
                paused_approval = None
                try:
                    for event in agent_runtime.stream_reply(
                        identity=identity,
                        session=session,
                        user_message=user_message,
                    ):
                        current_run_id = event.run_id or current_run_id
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
                            system_message = None
                            if event.event == "compression_completed":
                                system_message = session_service.append_system_message(
                                    identity,
                                    session_id,
                                    event.content,
                                    dedupe_recent_seconds=300,
                                )
                            yield _sse_event(
                                event.event,
                                {
                                    "run_id": event.run_id,
                                    "mode": event.mode,
                                    "message": event.content,
                                    "system_message": system_message,
                                    "details": event.details or {},
                                },
                            )
                            continue

                        if event.event == "approval_required":
                            approval_request = (event.details or {}).get("approval_request")
                            approval = approval_service.create_approval(
                                identity,
                                session,
                                run_id=event.run_id,
                                request_message_id=user_message.message_id,
                                interrupt=approval_interrupt_from_runtime(approval_request),
                            )
                            paused_approval = approval
                            yield _sse_event(
                                "approval.required",
                                {
                                    "run_id": event.run_id,
                                    "approval": approval,
                                },
                            )
                            continue

                        if event.event == "run_paused":
                            yield _sse_event(
                                "run.paused",
                                {
                                    "run_id": event.run_id,
                                    "mode": event.mode,
                                    "message": event.content,
                                },
                            )
                            final_event = event
                            break

                        if event.event == "completed":
                            final_event = event
                except AgentInvocationError as exc:
                    logger.exception("DeepAgent 流式调用失败")
                    error_payload = exc.to_payload()
                    ai_agent_message = session_service.append_ai_agent_message(
                        identity,
                        session_id,
                        _build_ai_agent_error_content(
                            error_payload,
                            request_id=request_id,
                            run_id=current_run_id,
                        ),
                    )
                    logger.debug(
                        "ai-agent error message persisted message_id=%s",
                        ai_agent_message.message_id,
                    )
                    yield _sse_event(
                        "error",
                        {
                            **error_payload,
                            "ai_agent_message": ai_agent_message,
                            "request_id": request_id,
                            "run_id": current_run_id,
                        },
                    )
                    return
                except Exception:
                    logger.exception("DeepAgent 流式调用出现未分类异常")
                    error_payload = {
                        "detail": "流式响应失败，请查看后端日志。",
                        "error_type": "stream_error",
                        "stage": "stream",
                    }
                    ai_agent_message = session_service.append_ai_agent_message(
                        identity,
                        session_id,
                        _build_ai_agent_error_content(
                            error_payload,
                            request_id=request_id,
                            run_id=current_run_id,
                        ),
                    )
                    logger.debug(
                        "ai-agent error message persisted message_id=%s",
                        ai_agent_message.message_id,
                    )
                    yield _sse_event(
                        "error",
                        {
                            **error_payload,
                            "ai_agent_message": ai_agent_message,
                            "request_id": request_id,
                            "run_id": current_run_id,
                        },
                    )
                    return

                if final_event is None:
                    logger.error("DeepAgent 流式调用未正常结束")
                    error_payload = {
                        "detail": "流式响应未正常结束。",
                        "error_type": "incomplete_stream",
                        "stage": "stream",
                    }
                    ai_agent_message = session_service.append_ai_agent_message(
                        identity,
                        session_id,
                        _build_ai_agent_error_content(
                            error_payload,
                            request_id=request_id,
                            run_id=current_run_id,
                        ),
                    )
                    logger.debug(
                        "ai-agent error message persisted message_id=%s",
                        ai_agent_message.message_id,
                    )
                    yield _sse_event(
                        "error",
                        {
                            **error_payload,
                            "ai_agent_message": ai_agent_message,
                            "request_id": request_id,
                            "run_id": current_run_id,
                        },
                    )
                    return

                if paused_approval is not None:
                    latest_session = session_service.get_session(identity, session_id).meta
                    yield _sse_event(
                        "done",
                        {
                            "session": latest_session,
                            "user_message": user_message,
                            "assistant_message": None,
                            "approval": paused_approval,
                            "run_id": final_event.run_id,
                            "mode": final_event.mode,
                            "warning": final_event.warning,
                            "paused": True,
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
                logger.debug(
                    "chat stream completed assistant_message_id=%s response_chars=%s",
                    assistant_message.message_id,
                    len(assistant_text),
                )
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
        finally:
            run_lock_context.__exit__(None, None, None)

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


def _validate_message_content(content: str, settings: Settings) -> str:
    if not content.strip():
        raise HTTPException(
            status_code=422,
            detail="消息内容不能为空。",
        )
    if len(content) > settings.message_max_chars:
        raise HTTPException(
            status_code=422,
            detail=f"消息长度不能超过 {settings.message_max_chars} 字符。",
        )
    return content


def _assert_no_pending_approval(
    identity: Identity,
    session_id: str,
    approval_service: ApprovalService,
) -> None:
    detail = approval_service.session_service.get_session(identity, session_id)
    if approval_service.has_failed_expired_resume(detail.meta):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "expired_approval_resume_failed",
                "detail": "当前 Session 的过期审批暂停点尚未清理完成，请稍后重试。",
            },
        )
    if any(approval.status == "pending" for approval in detail.approvals):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "session_has_pending_approval",
                "detail": "当前 Session 存在待确认操作，请先批准或拒绝后再发送消息。",
            },
        )


def _build_ai_agent_error_content(
    payload: dict[str, str],
    *,
    request_id: str,
    run_id: str | None = None,
) -> str:
    detail = payload.get("detail") or "调用失败。"
    return f"本轮 AI Agent 调用失败：{detail}"
