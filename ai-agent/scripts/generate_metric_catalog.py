#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


HEALTH_ENUM_VALUES = ["passing", "warning", "critical", "unknown"]
ROLE_ENUM_VALUES = ["master", "slave", "unknown"]

MYSQL_COMPONENTS = {"upsql", "updrdb_upsql"}
REDIS_COMPONENTS = {"upredis"}

PRODUCT_ALIASES = {
    "mysql": ["mysql", "MySQL", "数据库"],
    "redis": ["redis", "Redis", "缓存"],
    "container": ["container", "容器"],
    "host": ["host", "主机", "服务器"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate DBAAS metric catalog from tb_metrics_template SQL.")
    parser.add_argument("input", type=Path, help="Path to tb_metrics_template.sql")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("config/dbaas_metric_catalog.json"),
        help="Catalog JSON output path.",
    )
    parser.add_argument("--summary", action="store_true", help="Print generation summary.")
    args = parser.parse_args()

    rows = parse_sql(args.input.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    skipped_trigger = 0
    for row in rows:
        metric_key = row["name"]
        if metric_key.endswith(".trigger"):
            skipped_trigger += 1
            continue
        entries.append(to_catalog_entry(row))

    entries.sort(key=lambda item: item["metric_key"])
    args.output.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.summary:
        value_types = Counter(item["value_type"] for item in entries)
        service_types = Counter(item["service_type"] for item in entries)
        enum_count = sum(1 for item in entries if item["value_type"] == "enum")
        print(f"source_rows={len(rows)}")
        print(f"catalog_entries={len(entries)}")
        print(f"skipped_trigger={skipped_trigger}")
        print(f"enum_entries={enum_count}")
        print(f"value_types={dict(sorted(value_types.items()))}")
        print(f"top_service_types={service_types.most_common(12)}")


def parse_sql(sql: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prefix = "INSERT INTO `tb_metrics_template` VALUES"
    for line in sql.splitlines():
        if prefix not in line:
            continue
        values_start = line.index("(", line.index(prefix))
        values_end = line.rindex(")")
        fields = parse_values(line[values_start + 1 : values_end])
        if len(fields) != 6:
            raise ValueError(f"expected 6 fields, got {len(fields)}: {line}")
        rows.append(
            {
                "id": str(fields[0]),
                "name": str(fields[1]),
                "metric_class_id": str(fields[2]),
                "sql_value_type": str(fields[3]),
                "history": int(fields[4]),
                "description": "" if fields[5] is None else str(fields[5]),
            }
        )
    return rows


def parse_values(values: str) -> list[str | int | None]:
    fields: list[str | int | None] = []
    i = 0
    while i < len(values):
        while i < len(values) and values[i].isspace():
            i += 1
        if i >= len(values):
            break
        if values[i] == "'":
            value, i = parse_sql_string(values, i)
            fields.append(value)
        else:
            start = i
            while i < len(values) and values[i] != ",":
                i += 1
            token = values[start:i].strip()
            if token.upper() == "NULL":
                fields.append(None)
            else:
                fields.append(int(token))
        while i < len(values) and values[i].isspace():
            i += 1
        if i < len(values):
            if values[i] != ",":
                raise ValueError(f"expected comma near: {values[i:i + 20]}")
            i += 1
    return fields


def parse_sql_string(values: str, index: int) -> tuple[str, int]:
    chars: list[str] = []
    i = index + 1
    while i < len(values):
        char = values[i]
        if char == "\\" and i + 1 < len(values):
            chars.append(values[i + 1])
            i += 2
            continue
        if char == "'":
            if i + 1 < len(values) and values[i + 1] == "'":
                chars.append("'")
                i += 2
                continue
            return "".join(chars), i + 1
        chars.append(char)
        i += 1
    raise ValueError("unterminated SQL string")


def to_catalog_entry(row: dict[str, Any]) -> dict[str, Any]:
    metric_key = row["name"]
    sql_value_type = row["sql_value_type"]
    description = normalize_product_terms(row["description"])
    service_type = infer_service_type(metric_key)
    value_type = infer_value_type(metric_key, sql_value_type)
    display_name = clean_display_name(description) or metric_key

    entry: dict[str, Any] = {
        "metric_key": metric_key,
        "display_name": display_name,
        "service_type": service_type,
        "value_type": value_type,
        "unit": infer_unit(metric_key, description, value_type),
        "aliases": build_aliases(metric_key, display_name, description, service_type),
        "description": description,
    }
    if value_type == "enum":
        entry["enum_values"] = infer_enum_values(metric_key)
    return entry


def infer_service_type(metric_key: str) -> str:
    parts = metric_key.split(".")
    if not parts:
        return "unknown"
    if parts[0] == "container":
        return "container"
    if parts[0] == "host":
        return "host"
    if parts[0] == "instance" and len(parts) > 1:
        component = parts[1]
        if component in MYSQL_COMPONENTS:
            return "mysql"
        if component in REDIS_COMPONENTS:
            return "redis"
        return component
    return parts[0]


def infer_value_type(metric_key: str, sql_value_type: str) -> str:
    if sql_value_type in {"int64", "float64"}:
        return "number"
    if sql_value_type == "string" and is_enum_metric(metric_key):
        return "enum"
    return "string"


def is_enum_metric(metric_key: str) -> bool:
    parts = metric_key.split(".")
    if metric_key.endswith(".trigger"):
        return False
    if metric_key.endswith(".status"):
        return True
    if metric_key.endswith(".health"):
        return True
    if "status" in parts and parts[-1] in {"status", "health"}:
        return True
    return False


def infer_enum_values(metric_key: str) -> list[str]:
    if metric_key.endswith(".role.status"):
        return ROLE_ENUM_VALUES
    return HEALTH_ENUM_VALUES


def infer_unit(metric_key: str, description: str, value_type: str) -> str | None:
    if value_type != "number":
        return None
    text = f"{metric_key} {description}".casefold()
    if "bytes_per_sec" in text or "bytes/s" in text:
        return "bytes/s"
    if any(term in text for term in ["kbytes_per_sec", "kb/s"]):
        return "KB/s"
    if any(term in text for term in ["pps", "每秒"]):
        return "per_second"
    if any(term in text for term in ["pct", "percent", "percentage", "使用率", "百分比", "命中率", "健康度"]):
        return "%"
    if any(term in text for term in ["ratio", "碎片率"]):
        return "ratio"
    if any(term in text for term in ["bytes", "byte", "file_size", "space_size", "大小", "空间"]):
        return "bytes"
    if any(term in text for term in ["delay", "time_sec", "耗时", "延迟秒数", "时间"]):
        return "seconds"
    if any(term in text for term in ["count", "number", "数量", "总数", "个数"]):
        return "count"
    return None


def build_aliases(metric_key: str, display_name: str, description: str, service_type: str) -> list[str]:
    aliases: list[str] = []

    def add(value: str | None) -> None:
        if value is None:
            return
        item = value.strip()
        if item and item not in aliases:
            aliases.append(item)

    add(display_name)

    key_text = metric_key.casefold()
    desc_text = description.casefold()

    if "cpu" in key_text or "cpu" in desc_text:
        add("CPU")
        add("CPU使用率")
        add("CPU占用率")
    if "mem" in key_text or "内存" in description:
        add("内存")
        if "status" in key_text or "状态" in description:
            add("内存状态")
        else:
            add("memory")
        if is_usage_metric(key_text, description):
            add("内存使用率")
            add("内存占用率")
        if "used" in key_text or "使用的内存" in description:
            add("内存用量")
    if "disk" in key_text or "fs." in key_text or "空间" in description:
        add("磁盘")
        add("disk")
        add("storage")
        if is_usage_metric(key_text, description):
            add("空间使用率")
            if "datadir" in key_text or "表空间" in description:
                add("数据空间使用率")
                add("表空间使用率")
            if "logdir" in key_text or "日志空间" in description:
                add("日志空间使用率")
            if "rootdir" in key_text or "根目录" in description:
                add("根目录使用率")
        if "total" in key_text or "总量" in description:
            add("空间总量")
            add("容量")
            if "datadir" in key_text:
                add("数据空间总量")
                add("数据空间")
            if "logdir" in key_text:
                add("日志空间总量")
                add("日志空间")
            if "rootdir" in key_text:
                add("根目录总量")
    if "network" in key_text or "网络" in description:
        add("网络")
        add("network")
        if "receive" in key_text or "接收" in description:
            add("网络接收速度")
            add("network receive")
        if "transmit" in key_text or "发送" in description:
            add("网络发送速度")
            add("network transmit")
        if "status" in key_text or "状态" in description:
            add("网络连通状态")
            add("network status")
    if "connection" in key_text or "连接" in description:
        add("连接数")
        add("当前连接数")
        if "max" in key_text or "最大" in description:
            add("最大连接数")
        if is_usage_metric(key_text, description):
            add("连接数使用率")
    if "replication" in key_text or "复制" in description:
        add("复制状态")
        add("同步状态")
        add("主从同步")
        if "delay" in key_text or "behind_master" in key_text or "延迟" in description:
            add("复制延迟")
            add("同步延迟")
    if "running.status" in key_text:
        add("运行状态")
        add("健康状态")
        add("是否正常")
        add("异常状态")
    if "uptime.status" in key_text:
        add("重启状态")
        add("是否重启")
    if "role.status" in key_text:
        add("角色")
        add("角色状态")
    if "topology.status" in key_text:
        add("拓扑状态")
        add("拓扑版本")
    if "qps" in key_text or "qps" in desc_text:
        add("QPS")
    if "tps" in key_text or "tps" in desc_text:
        add("TPS")
    if "iops" in key_text or "iops" in desc_text:
        add("IOPS")
    if "buffer_pool" in key_text or "bufferpool" in desc_text:
        add("bufferpool")
        add("缓冲池")
        if "dirty_page" in key_text or "脏页" in description:
            add("脏页")
        if "hit" in key_text or "命中率" in description:
            add("命中率")
    if "prepared_statement" in key_text or "prepared_statement" in desc_text:
        add("预处理语句")
    if ("status" in key_text or "状态" in description) and display_name != "状态":
        add("状态")

    return aliases[:8]


def is_usage_metric(key_text: str, description: str) -> bool:
    desc_cf = description.casefold()
    if any(term in key_text for term in ["usage", "usage_pct", "used_percentage", "percent"]):
        return True
    return any(term in desc_cf for term in ["使用率", "百分比", "命中率", "健康度"])


def clean_display_name(description: str) -> str:
    text = description.strip()
    for suffix in ["监控项", "监控"]:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def normalize_product_terms(text: str) -> str:
    replacements = [
        ("updrdb_upsql", "MySQL"),
        ("upredis", "Redis"),
        ("REDIS", "Redis"),
        ("upsql", "MySQL"),
    ]
    normalized = text
    for source, target in replacements:
        normalized = normalized.replace(source, target)
    return normalized


if __name__ == "__main__":
    main()
