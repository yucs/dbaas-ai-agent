from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.sessions.models import ApprovalRecord, SessionMeta


@dataclass(frozen=True, slots=True)
class OperationRunContext:
    identity: Identity
    session: SessionMeta
    run_id: str
    operation_service: Any
    task_service: Any
    approval: ApprovalRecord | None = None


_current_operation_context: ContextVar[OperationRunContext | None] = ContextVar(
    "operation_run_context",
    default=None,
)


class OperationContextError(RuntimeError):
    """Raised when a write tool runs outside the operation context."""


@contextmanager
def operation_run_context(context: OperationRunContext) -> Iterator[None]:
    previous = _current_operation_context.get()
    _current_operation_context.set(context)
    try:
        yield
    finally:
        _current_operation_context.set(previous)


def require_operation_context() -> OperationRunContext:
    context = _current_operation_context.get()
    if context is None:
        raise OperationContextError("operation tool called without operation run context")
    return context
