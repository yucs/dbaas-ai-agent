from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from dbass_ai_agent.identity.models import Identity

from .constants import (
    ADMIN_SCOPE,
    HOSTS_KIND,
    SCHEMA_FILES,
    SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_KINDS,
    USER_SCOPE,
)
from .workspace import read_json_file


class DbaasSchemaError(RuntimeError):
    """Raised when schema loading or schema lookup fails."""


def schema_version(kind: str, *, scope: str = ADMIN_SCOPE) -> str:
    _require_supported_kind(kind)
    _require_supported_scope(scope)
    _require_kind_scope(kind, scope)
    return SCHEMA_VERSIONS[(kind, scope)]


def schema_path(kind: str, *, app_root: Path, scope: str = ADMIN_SCOPE) -> Path:
    _require_supported_kind(kind)
    _require_supported_scope(scope)
    _require_kind_scope(kind, scope)
    return (app_root / SCHEMA_FILES[(kind, scope)]).resolve()


@lru_cache(maxsize=16)
def load_schema(path: str) -> dict[str, Any]:
    payload = read_json_file(Path(path))
    if not isinstance(payload, dict):
        raise DbaasSchemaError(f"schema must be an object: {path}")
    return payload


def describe_schema(kind: str, *, app_root: Path, identity: Identity | None = None) -> dict[str, Any]:
    scope = scope_for_identity(identity)
    path = schema_path(kind, app_root=app_root, scope=scope)
    return {
        "kind": kind,
        "scope": scope,
        "schema_version": schema_version(kind, scope=scope),
        "schema": load_schema(str(path)),
    }


def _require_supported_kind(kind: str) -> None:
    if kind not in SUPPORTED_SCHEMA_KINDS:
        raise DbaasSchemaError(f"unsupported dbaas kind: {kind}")


def scope_for_identity(identity: Identity | None) -> str:
    if identity is not None and identity.role != ADMIN_SCOPE:
        return USER_SCOPE
    return ADMIN_SCOPE


def _require_supported_scope(scope: str) -> None:
    if scope not in {ADMIN_SCOPE, USER_SCOPE}:
        raise DbaasSchemaError(f"unsupported dbaas scope: {scope}")


def _require_kind_scope(kind: str, scope: str) -> None:
    if kind == HOSTS_KIND and scope != ADMIN_SCOPE:
        raise DbaasSchemaError("hosts schema is only available for admin scope")
