from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import BaseMessageChunk

from dbass_ai_agent.config import Settings
from dbass_ai_agent.infra.ids import new_run_id
from dbass_ai_agent.sessions.models import ChatMessage, SessionMeta

from .compression_events import CompressionNotice, capture_compression_notices
from .dbaas_guard import build_not_supported_message, classify_dbaas_request
from .factory import RuntimeArtifacts, build_runtime_artifacts


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
]


@dataclass(frozen=True, slots=True)
class AgentReply:
    run_id: str
    content: str
    mode: str
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    event: Literal[
        "started",
        "token",
        "compression_started",
        "compression_completed",
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
    def __init__(self, settings: Settings) -> None:
        self.artifacts: RuntimeArtifacts = build_runtime_artifacts(settings)

    def generate_reply(
        self,
        *,
        session: SessionMeta,
        user_message: ChatMessage,
    ) -> AgentReply:
        question = user_message.content.strip()
        classification = classify_dbaas_request(question)

        if classification == "dbaas_realtime":
            return AgentReply(
                run_id=new_run_id(),
                content=build_not_supported_message(),
                mode="deepagent",
                warning="mock-server-disabled",
            )

        try:
            reply = self._invoke_agent(session.thread_id, question)
        except AgentInvocationError:
            raise
        except Exception as exc:  # pragma: no cover - provider/network/runtime specific
            raise AgentInvocationError.from_exception(
                exc,
                fallback="调用真实 DeepAgent 运行时失败。",
                stage="invoke",
            ) from exc

        return AgentReply(
            run_id=new_run_id(),
            content=reply,
            mode="deepagent",
        )

    def stream_reply(
        self,
        *,
        session: SessionMeta,
        user_message: ChatMessage,
    ) -> Iterator[AgentStreamEvent]:
        run_id = new_run_id()
        mode = "deepagent"
        question = user_message.content.strip()
        classification = classify_dbaas_request(question)

        if classification == "dbaas_realtime":
            warning = "mock-server-disabled"
            content = build_not_supported_message()
            yield AgentStreamEvent(
                event="started",
                run_id=run_id,
                mode=mode,
                warning=warning,
            )
            yield AgentStreamEvent(
                event="token",
                run_id=run_id,
                mode=mode,
                content=content,
                warning=warning,
            )
            yield AgentStreamEvent(
                event="completed",
                run_id=run_id,
                mode=mode,
                content=content,
                warning=warning,
            )
            return

        yield AgentStreamEvent(event="started", run_id=run_id, mode=mode)

        parts: list[str] = []
        compression_notices: list[CompressionNotice] = []

        def _on_compression(notice: CompressionNotice) -> None:
            if notice.thread_id == session.thread_id:
                compression_notices.append(notice)

        try:
            with capture_compression_notices(_on_compression):
                for delta in self._stream_agent_text(session.thread_id, question):
                    yield from self._drain_compression_events(
                        run_id,
                        mode,
                        compression_notices,
                    )
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
            raise
        except Exception as exc:  # pragma: no cover - provider/network/runtime specific
            raise AgentInvocationError.from_exception(
                exc,
                fallback="调用真实 DeepAgent 运行时失败。",
                stage="stream",
            ) from exc

        content = "".join(parts)
        if not content.strip():
            content = "当前模型没有返回可展示的消息。"
        yield AgentStreamEvent(event="completed", run_id=run_id, mode=mode, content=content)

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
                    "history_path": notice.history_path,
                    "summary_chars": notice.summary_chars,
                },
            )

    async def aclose(self) -> None:
        self.artifacts.http_client.close()
        await self.artifacts.http_async_client.aclose()
        self.artifacts.connection.close()

    def _invoke_agent(self, thread_id: str, prompt: str) -> str:
        result = self.artifacts.agent.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        return self._extract_text(result)

    def _stream_agent_text(self, thread_id: str, prompt: str) -> Iterator[str]:
        stream = getattr(self.artifacts.agent, "stream", None)
        if not callable(stream):
            yield self._invoke_agent(thread_id, prompt)
            return

        input_payload = {"messages": [{"role": "user", "content": prompt}]}
        config = {"configurable": {"thread_id": thread_id}}
        try:
            events = stream(input_payload, config=config, stream_mode="messages")
        except TypeError:
            yield self._invoke_agent(thread_id, prompt)
            return

        emitted_chunk = False
        final_text = ""
        try:
            for event in events:
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
            yield self._invoke_agent(thread_id, prompt)

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
        if isinstance(event, tuple) and len(event) == 2:
            message, metadata = event
            return message, metadata if isinstance(metadata, dict) else {}
        return None, {}

    def _extract_text(self, result: Any) -> str:
        messages = result.get("messages", [])
        if not messages:
            return "当前模型没有返回可展示的消息。"

        last_message = messages[-1]
        content = getattr(last_message, "content", "")
        return self._content_to_text(content)

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
