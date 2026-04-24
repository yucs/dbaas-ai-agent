from __future__ import annotations

from datetime import datetime, timezone
from secrets import randbelow


MAX_USER_SEGMENT_LENGTH = 24


def _build_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _build_random_suffix() -> str:
    return f"{randbelow(1_000_000):06d}"


def _build_user_segment(user_id: str) -> str:
    compact = "".join(ch for ch in user_id.strip() if ch.isalnum() or ch in "._-")
    if not compact:
        return "user"
    return compact[:MAX_USER_SEGMENT_LENGTH]


def _build_user_scope(user_id: str) -> str:
    return f"{_build_user_segment(user_id)}_{_build_timestamp()}_{_build_random_suffix()}"


def _build_prefixed_id(prefix: str, scope: str) -> str:
    return f"{prefix}_{scope}"


def new_session_id(user_id: str) -> str:
    return _build_prefixed_id("sess", _build_user_scope(user_id))


def new_thread_id(user_id: str) -> str:
    return _build_prefixed_id("thread", _build_user_scope(user_id))


def new_session_thread_ids(user_id: str) -> tuple[str, str]:
    scope = _build_user_scope(user_id)
    return _build_prefixed_id("sess", scope), _build_prefixed_id("thread", scope)


def new_message_id() -> str:
    return _build_prefixed_id("msg", _build_user_scope("system"))


def new_run_id() -> str:
    return _build_prefixed_id("run", _build_user_scope("system"))
