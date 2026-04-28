from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import tool

from dbass_ai_agent.config import APP_ROOT, Settings
from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig, dbaas_config_from_settings
from .constants import SERVICES_KIND
from .query import query_dbaas_data
from .schema import describe_schema
from .visibility import ensure_visible_services


_current_identity: ContextVar[Identity | None] = ContextVar("dbaas_current_identity", default=None)
_current_run_state: ContextVar[DbaasToolRunState | None] = ContextVar(
    "dbaas_tool_run_state",
    default=None,
)
MAX_REFRESHING_ATTEMPTS = 3


@dataclass(slots=True)
class DbaasToolRunState:
    refreshing_attempts: dict[str, int] = field(default_factory=dict)


class DbaasToolContextError(RuntimeError):
    """Raised when a DBAAS tool is called without request identity context."""


@contextmanager
def dbaas_tool_identity(
    identity: Identity,
    *,
    state: DbaasToolRunState | None = None,
) -> Iterator[None]:
    previous = _current_identity.get()
    previous_state = _current_run_state.get()
    _current_identity.set(identity)
    _current_run_state.set(state or DbaasToolRunState())
    try:
        yield
    finally:
        _current_identity.set(previous)
        _current_run_state.set(previous_state)


def build_dbaas_tools(settings: Settings) -> list[Any]:
    config = dbaas_config_from_settings(settings)

    @tool("sync_services_tool")
    def sync_services_tool() -> dict[str, Any]:
        """确保当前用户可见的 DBAAS services 数据快照可用，并返回快照元信息；同一轮 refreshing 最多重试 3 次。"""

        meta = ensure_visible_services(config, _require_identity(), app_root=APP_ROOT)
        return _apply_refreshing_retry_limit(
            meta,
            _require_run_state(),
            kind=SERVICES_KIND,
        )

    @tool("query_dbaas_data_tool")
    def query_dbaas_data_tool(
        kind: str,
        jq_filter: str,
        max_preview_items: int | None = None,
    ) -> dict[str, Any]:
        """使用 jq 查询当前用户可见的 DBAAS 数据。首次查询某个 kind 前应先调用 describe_dbaas_schema_tool。不要传文件路径，只传 kind 和 jq_filter。"""

        return query_dbaas_data(
            config,
            _require_identity(),
            kind=kind,
            jq_filter=jq_filter,
            max_preview_items=max_preview_items,
        )

    @tool("describe_dbaas_schema_tool")
    def describe_dbaas_schema_tool(kind: str = SERVICES_KIND) -> dict[str, Any]:
        """返回 DBAAS 数据 schema 的字段说明摘要。"""

        return describe_schema(kind, app_root=APP_ROOT)

    return [sync_services_tool, query_dbaas_data_tool, describe_dbaas_schema_tool]


def _require_identity() -> Identity:
    identity = _current_identity.get()
    if identity is None:
        raise DbaasToolContextError("DBAAS tool called without identity context")
    return identity


def _require_run_state() -> DbaasToolRunState:
    state = _current_run_state.get()
    if state is None:
        raise DbaasToolContextError("DBAAS tool called without run state context")
    return state


def _apply_refreshing_retry_limit(
    meta: dict[str, Any],
    state: DbaasToolRunState,
    *,
    kind: str,
) -> dict[str, Any]:
    if meta.get("status") != "refreshing":
        state.refreshing_attempts[kind] = 0
        return meta

    attempt = state.refreshing_attempts.get(kind, 0) + 1
    state.refreshing_attempts[kind] = attempt
    next_meta = {
        **meta,
        "refreshing_attempt": attempt,
        "refreshing_max_attempts": MAX_REFRESHING_ATTEMPTS,
    }
    if attempt <= MAX_REFRESHING_ATTEMPTS:
        return next_meta

    return {
        **next_meta,
        "status": "refreshing_retry_exhausted",
        "message": (
            "服务列表仍在刷新，本轮已达到最多 3 次 refreshing 重试；"
            "请结束本轮回复并提示用户稍后再试。"
        ),
    }
