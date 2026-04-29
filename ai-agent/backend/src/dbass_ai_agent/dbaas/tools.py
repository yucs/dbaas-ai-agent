from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import APP_ROOT, Settings
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig, dbaas_config_from_settings
from .constants import SERVICES_KIND
from .query import query_dbaas_data
from .schema import describe_schema


_current_identity: ContextVar[Identity | None] = ContextVar("dbaas_current_identity", default=None)


class DbaasToolContextError(RuntimeError):
    """Raised when a DBAAS tool is called without request identity context."""


@contextmanager
def dbaas_tool_identity(
    identity: Identity,
) -> Iterator[None]:
    previous = _current_identity.get()
    _current_identity.set(identity)
    try:
        yield
    finally:
        _current_identity.set(previous)


def build_dbaas_tools(settings: Settings) -> list[Any]:
    config = dbaas_config_from_settings(settings)

    @tool("query_dbaas_data_tool")
    def query_dbaas_data_tool(
        kind: str,
        jq_filter: str,
        max_preview_items: int | None = None,
    ) -> dict[str, Any]:
        """使用 jq 查询当前用户可见的 DBAAS services 数据。

        当前仅支持 kind=services。
        首次构造 jq 前，如上下文没有 services schema，先调用 describe_dbaas_schema_tool；已有 schema 则复用。
        只传 kind、jq_filter 和必要的 max_preview_items。
        services 顶层是数组，jq 从 .[] 处理单个服务。
        结果 truncated=true 时，仅基于 preview 总结，并建议缩小查询条件。
        """

        return query_dbaas_data(
            config,
            _require_identity(),
            kind=kind,
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
        )

    @tool("describe_dbaas_schema_tool")
    def describe_dbaas_schema_tool(kind: str = SERVICES_KIND) -> dict[str, Any]:
        """返回 DBAAS services schema 字段说明。

        当前仅支持 kind=services。
        上下文已有可用 services schema 时复用；仅在缺失、不足、字段不确定、jq 字段错误或用户要求时再次调用。
        """

        return describe_schema(kind, app_root=APP_ROOT)

    return [query_dbaas_data_tool, describe_dbaas_schema_tool]


def _require_identity() -> Identity:
    identity = _current_identity.get()
    if identity is None:
        raise DbaasToolContextError("DBAAS tool called without identity context")
    return identity
