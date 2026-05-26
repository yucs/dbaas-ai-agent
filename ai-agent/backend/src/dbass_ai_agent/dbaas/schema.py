from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from dbass_ai_agent.identity.models import Identity

from .constants import ADMIN_SCOPE, SCHEMA_FILES, SCHEMA_VERSIONS, SUPPORTED_KINDS, USER_SCOPE
from .workspace import read_json_file


class DbaasSchemaError(RuntimeError):
    """Raised when schema loading or validation fails."""


def schema_version(kind: str, *, scope: str = ADMIN_SCOPE) -> str:
    _require_supported_kind(kind)
    _require_supported_scope(scope)
    return SCHEMA_VERSIONS[(kind, scope)]


def schema_path(kind: str, *, app_root: Path, scope: str = ADMIN_SCOPE) -> Path:
    _require_supported_kind(kind)
    _require_supported_scope(scope)
    return (app_root / SCHEMA_FILES[(kind, scope)]).resolve()


@lru_cache(maxsize=16)
def load_schema(path: str) -> dict[str, Any]:
    payload = read_json_file(Path(path))
    if not isinstance(payload, dict):
        raise DbaasSchemaError(f"schema must be an object: {path}")
    return payload


@lru_cache(maxsize=16)
def _validator(path: str) -> Draft202012Validator:
    schema = load_schema(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_payload(kind: str, payload: Any, *, app_root: Path, scope: str = ADMIN_SCOPE) -> None:
    path = str(schema_path(kind, app_root=app_root, scope=scope))
    errors = sorted(_validator(path).iter_errors(payload), key=lambda item: item.path)
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path) or "$"
    raise DbaasSchemaError(f"{kind} schema validation failed at {location}: {first.message}")


def describe_schema(kind: str, *, app_root: Path, identity: Identity | None = None) -> dict[str, Any]:
    scope = scope_for_identity(identity)
    path = schema_path(kind, app_root=app_root, scope=scope)
    schema = load_schema(str(path))
    return {
        "kind": kind,
        "scope": scope,
        "schema_version": schema_version(kind, scope=scope),
        "schema_path": str(path),
        "title": schema.get("title"),
        "description": schema.get("description"),
        "top_level_type": schema.get("type"),
        "fields": _service_fields(schema) if kind == "services" else [],
    }


def _service_fields(schema: dict[str, Any]) -> list[dict[str, str]]:
    service_schema = schema.get("$defs", {}).get("ServiceDetailResponse", {})
    properties = service_schema.get("properties", {})
    if not isinstance(properties, dict):
        return []
    return [
        {
            "name": name,
            "description": str(value.get("description", "")),
        }
        for name, value in properties.items()
        if isinstance(value, dict)
    ]


def _require_supported_kind(kind: str) -> None:
    if kind not in SUPPORTED_KINDS:
        raise DbaasSchemaError(f"unsupported dbaas kind: {kind}")


def scope_for_identity(identity: Identity | None) -> str:
    if identity is not None and identity.role != ADMIN_SCOPE:
        return USER_SCOPE
    return ADMIN_SCOPE


def _require_supported_scope(scope: str) -> None:
    if scope not in {ADMIN_SCOPE, USER_SCOPE}:
        raise DbaasSchemaError(f"unsupported dbaas scope: {scope}")
