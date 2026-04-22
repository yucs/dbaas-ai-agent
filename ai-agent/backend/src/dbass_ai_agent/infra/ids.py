from __future__ import annotations

from uuid import uuid4


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def new_session_id() -> str:
    return _new_id("sess")


def new_thread_id() -> str:
    return _new_id("thread")


def new_message_id() -> str:
    return _new_id("msg")


def new_run_id() -> str:
    return _new_id("run")
