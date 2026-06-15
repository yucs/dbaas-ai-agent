from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity, UserRole

from .backup_tools import build_backup_tools
from .capability_tools import build_capability_tools
from .cluster_tools import build_cluster_tools
from .host_tools import build_host_tools
from .metric_tools import build_metric_tools
from .precheck_tools import build_precheck_tools
from .service_tools import build_service_tools
from .write_tools import build_write_tools


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


def build_dbaas_tools(settings: Settings, role: UserRole) -> list[Any]:
    tools = [
        *build_service_tools(settings, _require_identity),
        *build_backup_tools(settings, _require_identity),
        *build_capability_tools(settings, _require_identity),
        *build_metric_tools(settings, _require_identity),
        *build_precheck_tools(settings, _require_identity),
        *build_write_tools(settings),
    ]
    if role == "admin":
        tools.extend(build_admin_only_tools(settings))
    return tools


def build_admin_only_tools(settings: Settings) -> list[Any]:
    return [
        *build_host_tools(settings, _require_identity),
        *build_cluster_tools(settings, _require_identity),
    ]


def _require_identity() -> Identity:
    identity = _current_identity.get()
    if identity is None:
        raise DbaasToolContextError("DBAAS tool called without identity context")
    return identity
