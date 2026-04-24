from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbass_ai_agent.config import Settings
from dbass_ai_agent.infra.ids import new_run_id
from dbass_ai_agent.sessions.models import ChatMessage, SessionMeta

from .dbaas_guard import build_not_supported_message, classify_dbaas_request
from .factory import RuntimeArtifacts, build_runtime_artifacts


@dataclass(frozen=True, slots=True)
class AgentReply:
    run_id: str
    content: str
    mode: str
    warning: str | None = None


class AgentInvocationError(RuntimeError):
    """Raised when invoking the DeepAgent runtime fails."""


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
        except Exception as exc:  # pragma: no cover - provider/network/runtime specific
            raise AgentInvocationError("调用真实 DeepAgent 运行时失败。") from exc

        return AgentReply(
            run_id=new_run_id(),
            content=reply,
            mode="deepagent",
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
