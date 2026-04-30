from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from dbass_ai_agent.config import APP_ROOT

from .metric_models import MetricCatalogEntry, MetricValueType
from .metric_workspace import METRIC_KEY_PATTERN


CATALOG_PATH = Path("backend/config/dbaas_metric_catalog.json")
SUPPORTED_VALUE_TYPES = {"number", "string", "enum", "boolean"}


class MetricCatalogError(RuntimeError):
    """Raised when metric catalog loading or validation fails."""


def describe_unit_metric_catalog(
    query: str,
    *,
    service_type: str | None = None,
    limit: int | None = 10,
    app_root: Path = APP_ROOT,
) -> dict[str, Any]:
    try:
        entries = load_metric_catalog(app_root=app_root)
    except MetricCatalogError as exc:
        return {
            "status": "error",
            "error_type": "metric_catalog_unavailable",
            "message": str(exc),
        }

    query_text = query.strip()
    if not query_text:
        return {
            "status": "error",
            "error_type": "metric_catalog_query_required",
            "message": "查询监控项 catalog 时必须提供 query。",
        }

    effective_limit = 10 if limit is None else max(1, min(limit, 50))
    scored = [
        (score, entry)
        for entry in entries
        if (score := _score_entry(entry, query_text, service_type=service_type)) > 0
    ]
    scored.sort(key=lambda item: (-item[0], item[1].metric_key))
    items = [entry.compact() for _, entry in scored[:effective_limit]]
    return {
        "status": "success",
        "query": query_text,
        "service_type": service_type,
        "items": items,
        "count": len(items),
        "truncated": len(scored) > effective_limit,
        "message": "监控项 catalog 查询完成。" if items else "未找到匹配的监控项。",
    }


def get_metric_catalog_entry(metric_key: str, *, app_root: Path = APP_ROOT) -> MetricCatalogEntry | None:
    for entry in load_metric_catalog(app_root=app_root):
        if entry.metric_key == metric_key:
            return entry
    return None


@lru_cache(maxsize=8)
def load_metric_catalog(*, app_root: Path = APP_ROOT) -> tuple[MetricCatalogEntry, ...]:
    catalog_file = app_root / CATALOG_PATH
    try:
        with catalog_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise MetricCatalogError(f"监控项 catalog 文件不存在：{catalog_file}") from exc
    except json.JSONDecodeError as exc:
        raise MetricCatalogError(f"监控项 catalog JSON 解析失败：{catalog_file}") from exc
    if not isinstance(payload, list):
        raise MetricCatalogError("监控项 catalog 顶层必须是数组。")

    entries = [_entry_from_raw(raw, index) for index, raw in enumerate(payload)]
    metric_keys = [entry.metric_key for entry in entries]
    duplicate_keys = sorted({key for key in metric_keys if metric_keys.count(key) > 1})
    if duplicate_keys:
        raise MetricCatalogError(f"监控项 catalog 存在重复 metric_key：{', '.join(duplicate_keys)}")
    return tuple(entries)


def clear_metric_catalog_cache() -> None:
    load_metric_catalog.cache_clear()


def _entry_from_raw(raw: Any, index: int) -> MetricCatalogEntry:
    if not isinstance(raw, dict):
        raise MetricCatalogError(f"监控项 catalog 第 {index} 项必须是对象。")
    metric_key = _required_string(raw, "metric_key", index)
    if METRIC_KEY_PATTERN.fullmatch(metric_key) is None:
        raise MetricCatalogError(f"metric_key '{metric_key}' 包含不支持的字符。")
    display_name = _required_string(raw, "display_name", index)
    value_type = _required_string(raw, "value_type", index)
    if value_type not in SUPPORTED_VALUE_TYPES:
        raise MetricCatalogError(f"metric_key '{metric_key}' 的 value_type 不支持：{value_type}")
    service_types = _string_list(raw, "service_types", index, required=True)
    aliases = _string_list(raw, "aliases", index, required=True)
    enum_values = _string_list(raw, "enum_values", index, required=value_type == "enum")
    return MetricCatalogEntry(
        metric_key=metric_key,
        display_name=display_name,
        service_types=tuple(service_types),
        value_type=value_type,  # type: ignore[arg-type]
        unit=_optional_string(raw, "unit", index),
        aliases=tuple(aliases),
        description=_optional_string(raw, "description", index),
        enum_values=tuple(enum_values),
        normal_values=tuple(_string_list(raw, "normal_values", index, required=False)),
        abnormal_values=tuple(_string_list(raw, "abnormal_values", index, required=False)),
    )


def _required_string(raw: dict[str, Any], field: str, index: int) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MetricCatalogError(f"监控项 catalog 第 {index} 项缺少有效字段：{field}")
    return value.strip()


def _optional_string(raw: dict[str, Any], field: str, index: int) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MetricCatalogError(f"监控项 catalog 第 {index} 项字段 {field} 必须是字符串。")
    return value


def _string_list(raw: dict[str, Any], field: str, index: int, *, required: bool) -> list[str]:
    value = raw.get(field)
    if value is None:
        if required:
            raise MetricCatalogError(f"监控项 catalog 第 {index} 项缺少有效字段：{field}")
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise MetricCatalogError(f"监控项 catalog 第 {index} 项字段 {field} 必须是非空字符串数组。")
    return value


def _score_entry(entry: MetricCatalogEntry, query: str, *, service_type: str | None) -> int:
    if service_type is not None and service_type not in entry.service_types and "container" not in entry.service_types:
        return 0
    query_cf = query.casefold()
    aliases = [alias.casefold() for alias in entry.aliases]
    if entry.metric_key.casefold() == query_cf:
        return 100
    if entry.display_name.casefold() == query_cf:
        return 90
    if query_cf in aliases:
        return 80
    if query_cf in entry.metric_key.casefold():
        return 70
    if query_cf in entry.display_name.casefold() or any(query_cf in alias for alias in aliases):
        return 60
    if entry.description and query_cf in entry.description.casefold():
        return 50
    enum_terms = [*entry.enum_values, *entry.normal_values, *entry.abnormal_values]
    if any(query_cf in term.casefold() for term in enum_terms):
        return 40
    return 0
