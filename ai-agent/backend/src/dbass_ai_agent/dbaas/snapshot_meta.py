from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .workspace import read_json_file


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def isoformat(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_meta_fresh(meta: dict[str, Any], *, now: datetime | None = None) -> bool:
    if meta.get("status") != "fresh":
        return False
    expires_at = meta.get("expires_at")
    if not isinstance(expires_at, str):
        return False
    try:
        return (now or utcnow()) <= parse_time(expires_at)
    except ValueError:
        return False


def read_meta(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = read_json_file(path)
    except (FileNotFoundError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload
