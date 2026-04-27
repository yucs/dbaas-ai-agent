from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from dbass_ai_agent.config import Settings


LOG_CONTEXT_FIELDS = (
    "request_id",
    "user_id",
    "role",
    "session_id",
    "thread_id",
    "run_id",
)
DEFAULT_LOG_CONTEXT = {field: "-" for field in LOG_CONTEXT_FIELDS}
SAFE_LOG_VALUE_PATTERN = re.compile(r"[^A-Za-z0-9._:/@-]+")
SAFE_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,]+"),
    re.compile(r"(?i)authorization\s*[:=]\s*['\"]?[^'\"\n,]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
]

_LOG_CONTEXT: ContextVar[dict[str, str]] = ContextVar(
    "dbass_ai_agent_log_context",
    default=DEFAULT_LOG_CONTEXT,
)


class ContextLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _LOG_CONTEXT.get()
        for field, default in DEFAULT_LOG_CONTEXT.items():
            if not hasattr(record, field):
                setattr(record, field, context.get(field, default))
        return True


class ContextFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone()
        if datefmt:
            return timestamp.strftime(datefmt)
        return timestamp.isoformat(timespec="milliseconds")


def setup_logging(settings: Settings) -> None:
    level = _parse_log_level(settings.log_level)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in list(root_logger.handlers):
        if getattr(handler, "_dbass_ai_agent_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    formatter = ContextFormatter(
        "%(asctime)s %(levelname)s "
        "request_id=%(request_id)s user_id=%(user_id)s role=%(role)s "
        "session_id=%(session_id)s thread_id=%(thread_id)s run_id=%(run_id)s "
        "%(name)s: %(message)s"
    )

    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        settings.log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    _configure_handler(file_handler, level, formatter)
    root_logger.addHandler(file_handler)

    if settings.log_enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        _configure_handler(console_handler, level, formatter)
        root_logger.addHandler(console_handler)

    logging.getLogger("dbass_ai_agent").setLevel(level)


def bind_log_context(**values: Any) -> Token[dict[str, str]]:
    current = dict(_LOG_CONTEXT.get())
    for key, value in values.items():
        if key in DEFAULT_LOG_CONTEXT:
            current[key] = sanitize_log_value(value)
    return _LOG_CONTEXT.set(current)


def reset_log_context(token: Token[dict[str, str]]) -> None:
    _LOG_CONTEXT.reset(token)


@contextmanager
def log_context(**values: Any) -> Iterator[None]:
    previous = dict(_LOG_CONTEXT.get())
    current = dict(previous)
    for key, value in values.items():
        if key in DEFAULT_LOG_CONTEXT:
            current[key] = sanitize_log_value(value)
    _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.set(previous)


def new_request_id() -> str:
    return f"req_{uuid4().hex[:16]}"


def extract_session_id_from_path(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) < 4 or parts[:3] != ["api", "v1", "sessions"]:
        return "-"

    candidate = parts[3]
    if not SAFE_SESSION_ID_PATTERN.fullmatch(candidate):
        return "-"
    return candidate


def sanitize_log_value(value: Any, *, max_length: int = 128) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    if not text:
        return "-"
    safe = SAFE_LOG_VALUE_PATTERN.sub("_", text)
    if len(safe) > max_length:
        return f"{safe[:max_length]}..."
    return safe


def redact_log_text(value: str, *, max_length: int = 1000) -> str:
    cleaned = value.replace("\r", "\\r").replace("\n", "\\n")
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)
    if len(cleaned) > max_length:
        return f"{cleaned[:max_length]}..."
    return cleaned


def elapsed_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


def _configure_handler(
    handler: logging.Handler,
    level: int,
    formatter: logging.Formatter,
) -> None:
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(ContextLogFilter())
    setattr(handler, "_dbass_ai_agent_handler", True)


def _parse_log_level(raw_level: str) -> int:
    normalized = raw_level.upper()
    if normalized == "WARN":
        normalized = "WARNING"
    level = logging.getLevelName(normalized)
    if isinstance(level, int):
        return level
    return logging.INFO
