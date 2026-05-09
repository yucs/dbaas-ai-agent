from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from langchain_core.messages import BaseMessageChunk
from langgraph.types import Command

from dbass_ai_agent.config import Settings
from dbass_ai_agent.dbaas.tools import dbaas_tool_identity
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.ids import new_run_id
from dbass_ai_agent.infra.logging import elapsed_ms, log_context, redact_log_text
from dbass_ai_agent.operations.context import OperationRunContext, operation_run_context
from dbass_ai_agent.sessions.models import ApprovalRecord, ChatMessage, SessionMeta

from .compression_events import CompressionNotice, capture_compression_notices
from .factory import RuntimeArtifacts, build_runtime_artifacts


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
]
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentApprovalRequest:
    action_request: dict[str, Any]
    review_config: dict[str, Any]
    tool_call_id: str
    interrupt_count: int = 1


@dataclass(frozen=True, slots=True)
class AgentRunOutput:
    content: str = ""
    approval_request: AgentApprovalRequest | None = None


@dataclass(frozen=True, slots=True)
class AgentReply:
    run_id: str
    content: str
    mode: str
    warning: str | None = None
    approval_request: AgentApprovalRequest | None = None
    paused: bool = False


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    event: Literal[
        "started",
        "token",
        "compression_started",
        "compression_completed",
        "approval_required",
        "run_paused",
        "run_resumed",
        "completed",
    ]
    run_id: str
    mode: str
    content: str = ""
    warning: str | None = None
    details: dict[str, Any] | None = None


class AgentInvocationError(RuntimeError):
    """Raised when invoking the DeepAgent runtime fails."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "agent_invocation_error",
        stage: str = "agent",
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.stage = stage

    def to_payload(self) -> dict[str, str]:
        return {
            "detail": str(self),
            "error_type": self.error_type,
            "stage": self.stage,
        }

    @classmethod
    def from_exception(
        cls,
        exc: Exception,
        *,
        fallback: str,
        stage: str,
    ) -> "AgentInvocationError":
        error_type = _classify_exception(exc)
        message = _format_public_error_message(exc, fallback=fallback, error_type=error_type)
        return cls(message, error_type=error_type, stage=stage)


class DeepAgentRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        operation_service: Any | None = None,
        task_service: Any | None = None,
    ) -> None:
        self.artifacts: RuntimeArtifacts = build_runtime_artifacts(settings)
        self.operation_service = operation_service
        self.task_service = task_service

    def generate_reply(
        self,
        *,
        identity: Identity,
        session: SessionMeta,
        user_message: ChatMessage,
    ) -> AgentReply:
        run_id = new_run_id()
        with log_context(
            session_id=session.session_id,
            thread_id=session.thread_id,
            run_id=run_id,
        ):
            question = user_message.content.strip()
            logger.info(
                "agent invoke started message_chars=%s user_input=%s",
                len(question),
                redact_log_text(question),
            )

            started_at = perf_counter()
            try:
                with (
                    dbaas_tool_identity(identity),
                    self._operation_context(identity, session, run_id),
                ):
                    output = self._normalize_run_output(
                        self._invoke_agent(session.thread_id, question)
                    )
            except AgentInvocationError:
                logger.exception("agent invoke failed")
                raise
            except Exception as exc:  # pragma: no cover - provider/network/runtime specific
                logger.exception("agent invoke failed")
                raise AgentInvocationError.from_exception(
                    exc,
                    fallback="调用真实 DeepAgent 运行时失败。",
                    stage="invoke",
                ) from exc

            logger.info(
                "agent invoke completed duration_ms=%s",
                elapsed_ms(started_at),
            )
            logger.debug("agent invoke response response_chars=%s", len(output.content))
            return AgentReply(
                run_id=run_id,
                content=output.content,
                mode="deepagent",
                approval_request=output.approval_request,
                paused=output.approval_request is not None,
            )

    def resume_approval(
        self,
        *,
        identity: Identity,
        session: SessionMeta,
        approval: ApprovalRecord,
        decision: Literal["approved", "rejected"],
        operation_service: Any,
        task_service: Any,
        reject_message: str | None = None,
    ) -> AgentReply:
        run_id = new_run_id()
        mode = "deepagent"
        resume_decision = _resume_decision_payload(decision, approval, reject_message=reject_message)
        with log_context(
            session_id=session.session_id,
            thread_id=session.thread_id,
            run_id=run_id,
        ):
            logger.info("agent resume started approval_id=%s decision=%s", approval.approval_id, decision)
            started_at = perf_counter()
            try:
                with (
                    dbaas_tool_identity(identity),
                    operation_run_context(
                        OperationRunContext(
                            identity=identity,
                            session=session,
                            run_id=run_id,
                            operation_service=operation_service,
                            task_service=task_service,
                            approval=approval,
                        )
                    ),
                ):
                    result = self.artifacts.agent.invoke(
                        Command(resume=resume_decision),
                        config={"configurable": {"thread_id": session.thread_id}},
                    )
            except Exception as exc:  # pragma: no cover - provider/network/runtime specific
                logger.exception("agent resume failed")
                raise AgentInvocationError.from_exception(
                    exc,
                    fallback="恢复 DeepAgent 审批执行失败。",
                    stage="resume",
                ) from exc

            output = self._normalize_run_output(result)
            if output.approval_request is not None:
                operation = operation_service.find_by_approval(session, approval.approval_id)
                logger.info(
                    "agent resume stopped before nested approval approval_id=%s operation_id=%s",
                    approval.approval_id,
                    operation.operation_id if operation else "-",
                )
                return AgentReply(
                    run_id=run_id,
                    content=_nested_approval_content(operation),
                    mode=mode,
                    warning="approval_nested_interrupt",
                )
            logger.info("agent resume completed duration_ms=%s", elapsed_ms(started_at))
            return AgentReply(run_id=run_id, content=output.content, mode=mode)

    def stream_reply(
        self,
        *,
        identity: Identity,
        session: SessionMeta,
        user_message: ChatMessage,
    ) -> Iterator[AgentStreamEvent]:
        run_id = new_run_id()
        mode = "deepagent"
        with log_context(
            session_id=session.session_id,
            thread_id=session.thread_id,
            run_id=run_id,
        ):
            question = user_message.content.strip()
            logger.info(
                "agent stream started message_chars=%s user_input=%s",
                len(question),
                redact_log_text(question),
            )

            yield AgentStreamEvent(event="started", run_id=run_id, mode=mode)

            parts: list[str] = []
            compression_notices: list[CompressionNotice] = []

            def _on_compression(notice: CompressionNotice) -> None:
                if notice.thread_id == session.thread_id:
                    compression_notices.append(notice)

            started_at = perf_counter()
            try:
                agent_stream = self._stream_agent_text(session.thread_id, question)
                while True:
                    try:
                        with (
                            capture_compression_notices(_on_compression),
                            dbaas_tool_identity(identity),
                            self._operation_context(identity, session, run_id),
                        ):
                            delta = next(agent_stream)
                    except StopIteration:
                        break
                    yield from self._drain_compression_events(
                        run_id,
                        mode,
                        compression_notices,
                    )
                    if isinstance(delta, AgentApprovalRequest):
                        yield AgentStreamEvent(
                            event="approval_required",
                            run_id=run_id,
                            mode=mode,
                            details={"approval_request": delta},
                        )
                        yield AgentStreamEvent(
                            event="run_paused",
                            run_id=run_id,
                            mode=mode,
                            content="本轮已暂停，等待人工确认。",
                        )
                        return
                    if not delta:
                        continue
                    parts.append(delta)
                    yield AgentStreamEvent(
                        event="token",
                        run_id=run_id,
                        mode=mode,
                        content=delta,
                    )
                yield from self._drain_compression_events(
                    run_id,
                    mode,
                    compression_notices,
                )
            except AgentInvocationError:
                logger.exception("agent stream failed")
                raise
            except Exception as exc:  # pragma: no cover - provider/network/runtime specific
                logger.exception("agent stream failed")
                raise AgentInvocationError.from_exception(
                    exc,
                    fallback="调用真实 DeepAgent 运行时失败。",
                    stage="stream",
                ) from exc

            content = "".join(parts)
            if not content.strip():
                content = "当前模型没有返回可展示的消息。"
            logger.info(
                "agent stream completed duration_ms=%s",
                elapsed_ms(started_at),
            )
            logger.debug("agent stream response response_chars=%s", len(content))
            yield AgentStreamEvent(event="completed", run_id=run_id, mode=mode, content=content)

    def _operation_context(self, identity: Identity, session: SessionMeta, run_id: str):
        operation_service = getattr(self, "operation_service", None)
        task_service = getattr(self, "task_service", None)
        if operation_service is None or task_service is None:
            return nullcontext()
        return operation_run_context(
            OperationRunContext(
                identity=identity,
                session=session,
                run_id=run_id,
                operation_service=operation_service,
                task_service=task_service,
            )
        )

    def _drain_compression_events(
        self,
        run_id: str,
        mode: str,
        notices: list[CompressionNotice],
    ) -> Iterator[AgentStreamEvent]:
        while notices:
            notice = notices.pop(0)
            is_started = notice.phase == "started"
            yield AgentStreamEvent(
                event="compression_started" if is_started else "compression_completed",
                run_id=run_id,
                mode=mode,
                content=(
                    "上下文较长，正在整理早期内容。"
                    if is_started
                    else "上下文已自动压缩，本会话会继续使用同一个 Session。"
                ),
                details={
                    "phase": notice.phase,
                    "thread_id": notice.thread_id,
                    "summarized_messages": notice.summarized_messages,
                    "keep": notice.keep,
                    "trigger": notice.trigger,
                    "summary_chars": notice.summary_chars,
                },
            )

    async def aclose(self) -> None:
        self.artifacts.http_client.close()
        await self.artifacts.http_async_client.aclose()
        self.artifacts.connection.close()

    def _invoke_agent(self, thread_id: str, prompt: str) -> AgentRunOutput:
        result = self.artifacts.agent.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        return self._normalize_run_output(result)

    def _stream_agent_text(self, thread_id: str, prompt: str) -> Iterator[str | AgentApprovalRequest]:
        stream = getattr(self.artifacts.agent, "stream", None)
        if not callable(stream):
            logger.debug("agent stream unavailable fallback=invoke")
            output = self._normalize_run_output(self._invoke_agent(thread_id, prompt))
            if output.approval_request:
                yield output.approval_request
            else:
                yield output.content
            return

        input_payload = {"messages": [{"role": "user", "content": prompt}]}
        config = {"configurable": {"thread_id": thread_id}}
        try:
            events = stream(input_payload, config=config, stream_mode=["messages", "updates"])
        except TypeError:
            logger.debug("agent stream type_error fallback=invoke")
            output = self._normalize_run_output(self._invoke_agent(thread_id, prompt))
            if output.approval_request:
                yield output.approval_request
            else:
                yield output.content
            return

        emitted_chunk = False
        final_text = ""
        try:
            for event in events:
                if self._is_stream_update(event):
                    approval_request = self._extract_approval_request_from_update(event)
                    if approval_request is not None:
                        yield approval_request
                        return
                    continue

                message, metadata = self._extract_stream_message(event)
                if not self._should_emit_stream_message(message, metadata):
                    continue

                text = self._content_to_stream_text(getattr(message, "content", ""))
                if not text:
                    continue

                if isinstance(message, BaseMessageChunk):
                    emitted_chunk = True
                    yield text
                elif not emitted_chunk:
                    final_text = text

            if not emitted_chunk and final_text:
                yield final_text
        except TypeError:
            if emitted_chunk:
                raise
            logger.debug("agent stream event_type_error fallback=invoke")
            output = self._normalize_run_output(self._invoke_agent(thread_id, prompt))
            if output.approval_request:
                yield output.approval_request
            else:
                yield output.content

    def _should_emit_stream_message(self, message: Any | None, metadata: dict[str, Any]) -> bool:
        if message is None:
            return False
        source = metadata.get("lc_source")
        nested_metadata = metadata.get("metadata")
        if source is None and isinstance(nested_metadata, dict):
            source = nested_metadata.get("lc_source")
        if source == "summarization":
            return False
        return True

    @staticmethod
    def _extract_stream_message(event: Any) -> tuple[Any | None, dict[str, Any]]:
        if (
            isinstance(event, tuple)
            and len(event) == 2
            and event[0] == "messages"
        ):
            event = event[1]
        if isinstance(event, tuple) and len(event) == 2:
            message, metadata = event
            return message, metadata if isinstance(metadata, dict) else {}
        return None, {}

    def _normalize_run_output(self, result: Any) -> AgentRunOutput:
        if isinstance(result, AgentRunOutput):
            return result
        if isinstance(result, str):
            return AgentRunOutput(content=result)
        approval_request = self._extract_approval_request(result)
        if approval_request is not None:
            return AgentRunOutput(content="", approval_request=approval_request)
        return AgentRunOutput(content=self._extract_text(result))

    def _extract_text(self, result: Any) -> str:
        messages = result.get("messages", [])
        if not messages:
            return "当前模型没有返回可展示的消息。"

        last_message = messages[-1]
        content = getattr(last_message, "content", "")
        return self._content_to_text(content)

    def _extract_approval_request(self, result: Any) -> AgentApprovalRequest | None:
        if not isinstance(result, dict):
            return None
        interrupts = result.get("__interrupt__")
        if not interrupts:
            return None
        interrupt = interrupts[0]
        value = getattr(interrupt, "value", None)
        if not isinstance(value, dict):
            return None
        action_requests = value.get("action_requests")
        review_configs = value.get("review_configs")
        if not isinstance(action_requests, list) or not action_requests:
            return None
        if not isinstance(review_configs, list) or not review_configs:
            return None
        action_request = action_requests[0]
        review_config = review_configs[0]
        if not isinstance(action_request, dict) or not isinstance(review_config, dict):
            return None
        tool_call_id = self._find_interrupted_tool_call_id(result, action_request)
        return AgentApprovalRequest(
            action_request=action_request,
            review_config=review_config,
            tool_call_id=tool_call_id,
            interrupt_count=len(action_requests),
        )

    def _extract_approval_request_from_update(self, event: Any) -> AgentApprovalRequest | None:
        if not self._is_stream_update(event):
            return None
        update = event[1]
        if not isinstance(update, dict) or "__interrupt__" not in update:
            return None
        return self._extract_approval_request(update)

    @staticmethod
    def _is_stream_update(event: Any) -> bool:
        return isinstance(event, tuple) and len(event) == 2 and event[0] == "updates"

    @staticmethod
    def _find_interrupted_tool_call_id(result: dict[str, Any], action_request: dict[str, Any]) -> str:
        name = action_request.get("name")
        args = action_request.get("args")
        messages = result.get("messages", [])
        for message in reversed(messages):
            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                continue
            for tool_call in tool_calls:
                if tool_call.get("name") == name and tool_call.get("args") == args:
                    return str(tool_call.get("id") or "")
            for tool_call in tool_calls:
                if tool_call.get("name") == name:
                    return str(tool_call.get("id") or "")
        return ""

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                    nested_text = item.get("content")
                    if isinstance(nested_text, str):
                        parts.append(nested_text)
                        continue
                    if item.get("type") == "text" and isinstance(item.get("value"), str):
                        parts.append(item["value"])
                        continue
                else:
                    text = getattr(item, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            merged = "\n".join(part.strip() for part in parts if part and part.strip())
            if merged:
                return merged
        return str(content)

    @staticmethod
    def _content_to_stream_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                    nested_text = item.get("content")
                    if isinstance(nested_text, str):
                        parts.append(nested_text)
            return "".join(parts)
        return ""


def _resume_decision_payload(
    decision: Literal["approved", "rejected"],
    approval: ApprovalRecord,
    *,
    reject_message: str | None = None,
) -> dict[str, list[dict[str, str]]]:
    decision_count = max(1, getattr(approval, "interrupt_count", 1))
    if decision == "approved":
        return {"decisions": [{"type": "approve"} for _ in range(decision_count)]}
    message = reject_message or "用户拒绝执行该操作。"
    return {"decisions": [{"type": "reject", "message": message} for _ in range(decision_count)]}


def _nested_approval_content(operation: Any | None) -> str:
    if operation is not None and getattr(operation, "result", None) is not None:
        summary = operation.result.summary
        return (
            f"{summary}\n\n"
            "后续还有新的写操作需要单独确认。当前版本不会在一次批准后连续弹出第二个确认，"
            "请继续发送下一步操作指令后再确认。"
        )
    return (
        "本次批准后的执行已结束。\n\n"
        "后续还有新的写操作需要单独确认。当前版本不会在一次批准后连续弹出第二个确认，"
        "请继续发送下一步操作指令后再确认。"
    )


def _classify_exception(exc: Exception) -> str:
    haystack = f"{type(exc).__module__}.{type(exc).__name__} {exc}".lower()
    if "tool" in haystack or "function" in haystack:
        return "function_error"
    if "timeout" in haystack:
        return "timeout_error"
    if "http" in haystack or "connect" in haystack or "network" in haystack:
        return "provider_error"
    return "agent_invocation_error"


def _format_public_error_message(
    exc: Exception,
    *,
    fallback: str,
    error_type: str,
) -> str:
    raw_message = _sanitize_exception_message(str(exc).strip())
    if not raw_message:
        return fallback

    clipped = raw_message[:300]
    if error_type == "function_error":
        return f"函数调用失败：{clipped}"
    if error_type == "timeout_error":
        return f"模型或工具调用超时：{clipped}"
    if error_type == "provider_error":
        return f"模型服务调用失败：{clipped}"
    return f"{fallback} {clipped}"


def _sanitize_exception_message(message: str) -> str:
    cleaned = message
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    return cleaned
