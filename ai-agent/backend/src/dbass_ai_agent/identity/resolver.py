from __future__ import annotations

import re

from fastapi import HTTPException, Request, status

from .models import Identity, UserRole


SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _require_safe_value(name: str, value: str) -> str:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{name} 仅支持字母、数字、点、下划线和中划线，长度不超过 64。",
        )
    return value


def resolve_identity(request: Request) -> Identity:
    raw_user_id = request.headers.get("X-User-Id", "").strip()
    raw_role = request.headers.get("X-User-Role", "user").strip().lower()
    raw_user = request.headers.get("X-User", "").strip()

    if not raw_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 X-User-Id 请求头。",
        )

    user_id = _require_safe_value("user_id", raw_user_id)

    if raw_role not in {"admin", "user"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Role 仅支持 admin 或 user。",
        )

    role: UserRole = raw_role  # type: ignore[assignment]

    if role == "admin":
        user = _require_safe_value("user", raw_user) if raw_user else None
        return Identity(user_id=user_id, role=role, user=user)

    user = _require_safe_value("user", raw_user) if raw_user else user_id
    return Identity(user_id=user_id, role=role, user=user)
