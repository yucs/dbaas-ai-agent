from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from dbass_ai_agent.config import APP_ROOT

from .constants import METRIC_CATALOG_FILE
from .metric_models import MetricCatalogEntry, MetricValueType
from .metric_workspace import METRIC_KEY_PATTERN
from .workspace import read_json_file


SUPPORTED_VALUE_TYPES = {"number", "string", "enum", "boolean"}
GLOBAL_UNIT_METRIC_SERVICE_TYPES = {"container"}
HOST_METRIC_SERVICE_TYPES = {"host"}


class MetricCatalogError(RuntimeError):
    """Raised when metric catalog loading or validation fails."""


def describe_unit_metric_catalog(
    query: str | None = None,
    *,
    service_type: str | None = None,
    limit: int | None = None,
    app_root: Path = APP_ROOT,
) -> dict[str, Any]:
    service_type_filter = service_type.strip() if service_type and service_type.strip() else None
    if service_type_filter is None:
        return {
            "status": "error",
            "error_type": "metric_catalog_service_type_required",
            "message": "查询监控项 catalog 时必须提供 service_type。",
        }

    try:
        entries = load_metric_catalog(app_root=app_root)
    except MetricCatalogError as exc:
        return {
            "status": "error",
            "error_type": "metric_catalog_unavailable",
            "message": str(exc),
        }

    query_text = (query or "").strip()
    list_mode = not query_text
    effective_limit = (50 if list_mode else 10) if limit is None else max(1, min(limit, 50))
    if list_mode:
        matched = [entry for entry in entries if _matches_service_type(entry, service_type_filter)]
        matched.sort(key=lambda entry: entry.metric_key)
        items = [entry.compact() for entry in matched[:effective_limit]]
        total = len(matched)
    else:
        scored = [
            (score, entry)
            for entry in entries
            if (score := _score_entry(entry, query_text, service_type=service_type_filter)) > 0
        ]
        scored.sort(key=lambda item: (-item[0], item[1].metric_key))
        items = [entry.compact() for _, entry in scored[:effective_limit]]
        total = len(scored)
    return {
        "status": "success",
        "mode": "list" if list_mode else "search",
        "query": query_text,
        "service_type": service_type_filter,
        "catalog_semantics": metric_catalog_semantics(),
        "data_shapes": metric_data_shapes(),
        "items": items,
        "count": len(items),
        "truncated": total > effective_limit,
        "message": _catalog_result_message(items=items, list_mode=list_mode),
    }


def metric_catalog_semantics() -> dict[str, Any]:
    return {
        "service_type_semantics": {
            "container": (
                "所有 DBAAS 单元通用的容器级监控指标域。用户询问 mysql、redis 等服务的 CPU、内存、"
                "磁盘、网络等资源指标时，也应接受 service_type=container 的 catalog 条目；"
                "查询 latest 数据后，再用数据项里的 service_type 过滤真实业务服务类型。"
            ),
            "host": "主机级监控指标域，不等同于单元所属业务服务类型；查询 host catalog 时不混入 container 指标。",
            "mysql/redis/...": "实例或组件级监控指标域，通常用于运行状态、复制状态、连接数等组件语义。",
        },
        "query_guidance": [
            "service_type 必填；query 为空时列出该 service_type 下可用的监控指标，query 非空时按关键词搜索。",
            "用户问某服务类型的 CPU、内存、磁盘、网络等资源使用情况时，catalog 可返回 service_type=container 的通用单元指标。",
            '例如用户问 mysql CPU 超过 80 的单元，应使用 container CPU 指标，并在 jq 中过滤 .service_type == "mysql"。',
            "不要把 catalog item 的 service_type=container 理解为只适用于名为 container 的业务服务。",
        ],
        "global_unit_metric_service_types": sorted(GLOBAL_UNIT_METRIC_SERVICE_TYPES),
    }


def metric_data_shapes() -> dict[str, Any]:
    return {
        "latest": {
            "top_level": "array",
            "jq_entry": ".[]",
            "has_data_wrapper": False,
            "description": "latest 数据视图顶层直接是数组；每个元素表示一个单元的当前监控值。",
            "item_fields": [
                {
                    "name": "service_name",
                    "description": "服务名称。",
                    "type": "string",
                    "nullable": False,
                },
                {
                    "name": "unit_name",
                    "description": "单元名称。",
                    "type": "string",
                    "nullable": False,
                },
                {
                    "name": "service_type",
                    "description": "服务或子服务类型。",
                    "type": "string",
                    "nullable": False,
                },
                {
                    "name": "value",
                    "description": "监控值，具体类型、单位和枚举语义由 catalog item 的 value_type、unit、enum_values 等字段决定。",
                    "type": ["number", "string", "boolean", "null"],
                    "nullable": True,
                },
            ],
        },
        "history": {
            "top_level": "array",
            "jq_entry": ".[]",
            "has_data_wrapper": False,
            "description": "history 数据视图顶层直接是数组，不存在 .data 包装层；每个元素表示一个历史点位。",
            "item_fields": [
                {
                    "name": "ts",
                    "description": "历史点位 Unix timestamp 秒数。",
                    "type": "number",
                    "nullable": False,
                },
                {
                    "name": "value",
                    "description": "历史点位监控值，具体类型、单位和枚举语义由 catalog item 的 value_type、unit、enum_values 等字段决定。",
                    "type": ["number", "string", "boolean", "null"],
                    "nullable": True,
                },
            ],
        },
    }


def get_metric_catalog_entry(metric_key: str, *, app_root: Path = APP_ROOT) -> MetricCatalogEntry | None:
    for entry in load_metric_catalog(app_root=app_root):
        if entry.metric_key == metric_key:
            return entry
    return None


@lru_cache(maxsize=8)
def load_metric_catalog(*, app_root: Path = APP_ROOT) -> tuple[MetricCatalogEntry, ...]:
    catalog_file = app_root / METRIC_CATALOG_FILE
    try:
        payload = read_json_file(catalog_file)
    except FileNotFoundError as exc:
        raise MetricCatalogError(f"监控项 catalog 文件不存在：{catalog_file}") from exc
    except ValueError as exc:
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
    service_type = _required_string(raw, "service_type", index)
    aliases = _string_list(raw, "aliases", index, required=True)
    enum_values = _string_list(raw, "enum_values", index, required=value_type == "enum")
    return MetricCatalogEntry(
        metric_key=metric_key,
        display_name=display_name,
        service_type=service_type,
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
    if not _matches_service_type(entry, service_type):
        return 0
    query_cf = query.casefold()
    aliases = [alias.casefold() for alias in entry.aliases]
    if entry.metric_key.casefold() == query_cf:
        return 100
    if entry.display_name.casefold() == query_cf:
        return 90
    if _compact_text(entry.display_name) == _compact_text(query_cf):
        return 88
    if query_cf in entry.display_name.casefold():
        return 82
    if entry.description and query_cf in entry.description.casefold():
        return 76
    if query_cf in aliases:
        return 70
    if any(_compact_text(alias) == _compact_text(query_cf) for alias in aliases):
        return 68
    if query_cf in entry.metric_key.casefold():
        return 65
    if any(query_cf in alias for alias in aliases):
        return 60
    if _all_query_terms_match(entry, query_cf):
        return 45
    enum_terms = [*entry.enum_values, *entry.normal_values, *entry.abnormal_values]
    if any(query_cf in term.casefold() for term in enum_terms):
        return 40
    return 0


def _matches_service_type(entry: MetricCatalogEntry, service_type: str | None) -> bool:
    if service_type is None:
        return False
    entry_type = entry.service_type.casefold()
    requested_type = service_type.casefold()
    return entry_type == requested_type or (
        entry_type in GLOBAL_UNIT_METRIC_SERVICE_TYPES
        and requested_type not in GLOBAL_UNIT_METRIC_SERVICE_TYPES
        and requested_type not in HOST_METRIC_SERVICE_TYPES
    )


def _catalog_result_message(*, items: list[dict[str, Any]], list_mode: bool) -> str:
    if items:
        return "监控项 catalog 列表完成。" if list_mode else "监控项 catalog 查询完成。"
    return "未找到匹配的监控项。"


def _all_query_terms_match(entry: MetricCatalogEntry, query_cf: str) -> bool:
    terms = [term for term in query_cf.replace("_", " ").replace(".", " ").split() if term]
    if len(terms) < 2:
        return False
    searchable = " ".join(
        [
            entry.metric_key,
            entry.display_name,
            entry.service_type,
            *(entry.aliases),
            entry.description or "",
            *(entry.enum_values),
            *(entry.normal_values),
            *(entry.abnormal_values),
        ]
    ).casefold()
    return all(term in searchable for term in terms)


def _compact_text(value: str) -> str:
    return "".join(value.casefold().split())
