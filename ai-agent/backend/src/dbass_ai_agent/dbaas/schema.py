from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .constants import SCHEMA_FILES, SCHEMA_VERSIONS, SUPPORTED_KINDS
from .workspace import read_json_file


class DbaasSchemaError(RuntimeError):
    """Raised when schema loading or validation fails."""


def schema_version(kind: str) -> str:
    _require_supported_kind(kind)
    return SCHEMA_VERSIONS[kind]


def schema_path(kind: str, *, app_root: Path) -> Path:
    _require_supported_kind(kind)
    return (app_root / SCHEMA_FILES[kind]).resolve()


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


def validate_payload(kind: str, payload: Any, *, app_root: Path) -> None:
    path = str(schema_path(kind, app_root=app_root))
    errors = sorted(_validator(path).iter_errors(payload), key=lambda item: item.path)
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.absolute_path) or "$"
    raise DbaasSchemaError(f"{kind} schema validation failed at {location}: {first.message}")


def describe_schema(kind: str, *, app_root: Path) -> dict[str, Any]:
    path = schema_path(kind, app_root=app_root)
    schema = load_schema(str(path))
    return {
        "kind": kind,
        "schema_version": schema_version(kind),
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
