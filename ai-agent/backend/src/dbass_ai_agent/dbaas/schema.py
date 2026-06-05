from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from dbass_ai_agent.identity.models import Identity

from .constants import (
    ADMIN_SCOPE,
    BACKUPS_KIND,
    SCHEMA_FILES,
    SCHEMA_VERSIONS,
    SERVICES_KIND,
    SUPPORTED_SCHEMA_KINDS,
    USER_SCOPE,
)
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
        "fields": _schema_fields(kind, schema),
    }


def _schema_fields(kind: str, schema: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == SERVICES_KIND:
        service_schema = schema.get("$defs", {}).get("ServiceDetailResponse", {})
        properties = service_schema.get("properties", {})
        return _field_summaries(properties)
    if kind == BACKUPS_KIND:
        item_schema = schema.get("$defs", {}).get("BackupRecord", {})
        properties = item_schema.get("properties", {})
        return _field_summaries(properties)
    return []


def _field_summaries(properties: Any) -> list[dict[str, Any]]:
    if not isinstance(properties, dict):
        return []
    fields: list[dict[str, Any]] = []
    for name, value in properties.items():
        if not isinstance(value, dict):
            continue
        field = {
            "name": name,
            "description": str(value.get("description", "")),
        }
        type_info = _field_type_summary(value)
        if type_info is not None:
            field["type"] = type_info["type"]
            field["nullable"] = type_info["nullable"]
        enum_values = _field_enum_summary(value)
        if enum_values:
            field["enum_values"] = enum_values
        fields.append(field)
    return fields


def _field_type_summary(field_schema: dict[str, Any]) -> dict[str, Any] | None:
    raw_type = field_schema.get("type")
    if isinstance(raw_type, str):
        return {"type": raw_type, "nullable": False}
    if isinstance(raw_type, list):
        types = [value for value in raw_type if isinstance(value, str)]
        non_null_types = [value for value in types if value != "null"]
        return {
            "type": non_null_types[0] if len(non_null_types) == 1 else non_null_types,
            "nullable": "null" in types,
        }
    return None


def _field_enum_summary(field_schema: dict[str, Any]) -> list[Any]:
    enum_values = field_schema.get("enum")
    if not isinstance(enum_values, list):
        return []
    return enum_values


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
