from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from dbass_ai_agent.agent.compression_events import CompressionNotice, publish_compression_notice
from dbass_ai_agent.config import Settings

from .prompt import load_compression_prompt, load_system_prompt


logger = logging.getLogger(__name__)


class AgentFactoryError(RuntimeError):
    """Raised when the DeepAgent runtime cannot be initialized."""


@dataclass(frozen=True, slots=True)
class RuntimeArtifacts:
    agent: Any
    connection: sqlite3.Connection
    http_client: httpx.Client
    http_async_client: httpx.AsyncClient


@dataclass(frozen=True, slots=True)
class RuntimeResources:
    connection: sqlite3.Connection
    checkpointer: Any
    http_client: httpx.Client
    http_async_client: httpx.AsyncClient


@dataclass(frozen=True, slots=True)
class RuntimeModels:
    main: Any
    summary: Any


def _build_logged_summarization_middleware_class() -> type[Any]:
    from deepagents.middleware.summarization import SummarizationMiddleware

    class LoggedSummarizationMiddleware(SummarizationMiddleware):
        """App-specific wrapper that emits an info log when compression occurs."""

        def _before_summarize(self, messages_to_summarize: list[Any]) -> None:
            thread_id = self._get_thread_id()
            history_path = self._get_history_path()
            logger.info(
                "会话上下文开始压缩 thread_id=%s summarized_messages=%d keep=%s "
                "trigger=%s history_path=%s",
                thread_id,
                len(messages_to_summarize),
                self._lc_helper.keep,
                self._lc_helper.trigger,
                history_path,
            )
            publish_compression_notice(
                CompressionNotice(
                    phase="started",
                    thread_id=thread_id,
                    summarized_messages=len(messages_to_summarize),
                    keep=str(self._lc_helper.keep),
                    trigger=str(self._lc_helper.trigger),
                )
            )

        def _on_summary(self, messages_to_summarize: list[Any], summary: str) -> None:
            thread_id = self._get_thread_id()
            history_path = self._get_history_path()
            summarized_messages = len(messages_to_summarize)
            summary_chars = len(summary)
            logger.info(
                "会话上下文已压缩 thread_id=%s summarized_messages=%d keep=%s "
                "trigger=%s history_path=%s summary_chars=%d",
                thread_id,
                summarized_messages,
                self._lc_helper.keep,
                self._lc_helper.trigger,
                history_path,
                summary_chars,
            )
            logger.info(
                "会话上下文压缩摘要 thread_id=%s summary=%s",
                thread_id,
                summary,
            )
            publish_compression_notice(
                CompressionNotice(
                    phase="completed",
                    thread_id=thread_id,
                    summarized_messages=summarized_messages,
                    keep=str(self._lc_helper.keep),
                    trigger=str(self._lc_helper.trigger),
                    summary_chars=summary_chars,
                )
            )

        def _create_summary(self, messages_to_summarize: list[Any]) -> str:
            self._before_summarize(messages_to_summarize)
            summary = super()._create_summary(messages_to_summarize)
            self._on_summary(messages_to_summarize, summary)
            return summary

        async def _acreate_summary(self, messages_to_summarize: list[Any]) -> str:
            self._before_summarize(messages_to_summarize)
            summary = await super()._acreate_summary(messages_to_summarize)
            self._on_summary(messages_to_summarize, summary)
            return summary

    return LoggedSummarizationMiddleware


def delete_thread_checkpoint(settings: Settings, thread_id: str) -> None:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise AgentFactoryError(
            "删除 DeepAgent 线程数据失败，缺少 langgraph-checkpoint-sqlite 依赖。"
        ) from exc

    checkpoint_path = settings.checkpoint_db
    if not checkpoint_path.exists():
        return

    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        SqliteSaver(connection).delete_thread(thread_id)
    finally:
        connection.close()


def build_runtime_artifacts(settings: Settings) -> RuntimeArtifacts:
    _validate_runtime_settings(settings)
    create_deep_agent, sqlite_saver_cls = _load_runtime_dependencies()
    resources = _create_runtime_resources(settings, sqlite_saver_cls=sqlite_saver_cls)
    models = _build_runtime_models(
        settings,
        http_client=resources.http_client,
        http_async_client=resources.http_async_client,
    )
    agent = _create_runtime_agent(
        settings,
        create_deep_agent=create_deep_agent,
        model=models.main,
        summary_model=models.summary,
        checkpointer=resources.checkpointer,
    )

    return RuntimeArtifacts(
        agent=agent,
        connection=resources.connection,
        http_client=resources.http_client,
        http_async_client=resources.http_async_client,
    )


def _validate_runtime_settings(settings: Settings) -> None:
    if settings.provider_kind != "openai_compatible":
        raise AgentFactoryError(
            f"当前仅支持 openai_compatible provider，收到: {settings.provider_kind}"
        )

    if not settings.model:
        raise AgentFactoryError("缺少模型配置 `model.model`。")
    if not settings.base_url:
        raise AgentFactoryError("缺少模型配置 `model.base_url`。")
    if not settings.api_key:
        raise AgentFactoryError("缺少模型配置 `model.api_key`。")


def _load_runtime_dependencies() -> tuple[Callable[..., Any], type[Any]]:
    try:
        from deepagents import create_deep_agent
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:
        raise AgentFactoryError(
            "DeepAgent 运行依赖未安装，请安装 deepagents、langchain-openai 和 "
            "langgraph-checkpoint-sqlite。"
        ) from exc

    return create_deep_agent, SqliteSaver


def _create_runtime_resources(
    settings: Settings,
    *,
    sqlite_saver_cls: type[Any],
) -> RuntimeResources:
    checkpoint_path = settings.checkpoint_db
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    return RuntimeResources(
        connection=connection,
        checkpointer=sqlite_saver_cls(connection),
        http_client=httpx.Client(trust_env=False),
        http_async_client=httpx.AsyncClient(trust_env=False),
    )


def _build_runtime_models(
    settings: Settings,
    *,
    http_client: httpx.Client,
    http_async_client: httpx.AsyncClient,
) -> RuntimeModels:
    return RuntimeModels(
        main=_build_chat_model(
            settings,
            http_client=http_client,
            http_async_client=http_async_client,
            max_completion_tokens=settings.max_output_tokens,
        ),
        summary=_build_chat_model(
            settings,
            http_client=http_client,
            http_async_client=http_async_client,
            max_completion_tokens=max(1, settings.summary_max_tokens),
        ),
    )


def _create_runtime_agent(
    settings: Settings,
    *,
    create_deep_agent: Callable[..., Any],
    model: Any,
    summary_model: Any,
    checkpointer: Any,
) -> Any:
    system_prompt = load_system_prompt(settings.system_prompt_path)
    summarization_factory = build_summarization_middleware_factory(
        settings,
        summary_model=summary_model,
    )
    with patch_deepagents_summarization_factory(summarization_factory):
        return create_deep_agent(
            model=model,
            checkpointer=checkpointer,
            system_prompt=system_prompt,
        )


def _build_chat_model(
    settings: Settings,
    *,
    http_client: httpx.Client,
    http_async_client: httpx.AsyncClient,
    max_completion_tokens: int,
) -> Any:
    from langchain_openai import ChatOpenAI

    extra_body = None
    if settings.thinking_enabled is not None:
        extra_body = {
            "thinking": {
                "type": "enabled" if settings.thinking_enabled else "disabled",
            }
        }

    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=0.2,
        max_completion_tokens=max_completion_tokens,
        extra_body=extra_body,
        http_client=http_client,
        http_async_client=http_async_client,
        http_socket_options=(),
    )


def build_summarization_middleware_factory(
    settings: Settings,
    *,
    summary_model: Any,
) -> Callable[[Any, Any], Any]:
    summary_prompt = load_compression_prompt(settings.compression_prompt_path)
    trigger_tokens = max(1, settings.soft_trigger_tokens)
    keep_recent_messages = max(1, settings.keep_recent_messages)

    def _factory(_model: Any, backend: Any) -> Any:
        if not settings.compression_enabled:
            from langchain.agents.middleware.types import AgentMiddleware

            class DisabledSummarizationMiddleware(AgentMiddleware[Any, Any, Any]):
                """No-op placeholder used when app-level compression is disabled."""

            return DisabledSummarizationMiddleware()

        LoggedSummarizationMiddleware = _build_logged_summarization_middleware_class()

        return LoggedSummarizationMiddleware(
            model=summary_model,
            backend=backend,
            trigger=("tokens", trigger_tokens),
            keep=("messages", keep_recent_messages),
            summary_prompt=summary_prompt,
            trim_tokens_to_summarize=None,
        )

    return _factory


@contextmanager
def patch_deepagents_summarization_factory(
    factory: Callable[[Any, Any], Any],
) -> Iterator[None]:
    import deepagents.graph as deepagents_graph

    original_factory = deepagents_graph.create_summarization_middleware
    deepagents_graph.create_summarization_middleware = factory
    try:
        yield
    finally:
        deepagents_graph.create_summarization_middleware = original_factory
