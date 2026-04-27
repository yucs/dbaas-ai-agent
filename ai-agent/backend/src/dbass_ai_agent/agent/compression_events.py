from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CompressionNotice:
    phase: Literal["started", "completed"]
    thread_id: str
    summarized_messages: int
    keep: str
    trigger: str
    summary_chars: int | None = None


CompressionNoticeHandler = Callable[[CompressionNotice], None]

_compression_notice_handler: ContextVar[CompressionNoticeHandler | None] = ContextVar(
    "compression_notice_handler",
    default=None,
)


@contextmanager
def capture_compression_notices(handler: CompressionNoticeHandler) -> Iterator[None]:
    previous = _compression_notice_handler.get()
    _compression_notice_handler.set(handler)
    try:
        yield
    finally:
        _compression_notice_handler.set(previous)


def publish_compression_notice(notice: CompressionNotice) -> None:
    handler = _compression_notice_handler.get()
    if handler is None:
        return
    handler(notice)
