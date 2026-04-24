from __future__ import annotations

import re
from typing import Literal


GuardDecision = Literal["normal", "dbaas_concept", "dbaas_realtime"]


DBAAS_RESOURCE_KEYWORDS = {
    "dbaas",
    "mock-server",
    "service",
    "cluster",
    "host",
    "task",
    "database",
    "db",
    "服务",
    "集群",
    "主机",
    "数据库",
    "实例",
    "任务",
    "mysql",
    "postgres",
    "postgresql",
    "redis",
    "mongodb",
    "mongo",
    "oracle",
    "tidb",
    "clickhouse",
}

DBAAS_RESOURCE_NAME_PATTERN = re.compile(
    r"\b(?:mysql|postgres|postgresql|redis|mongodb|mongo|oracle|tidb|clickhouse)[-_][a-z0-9][a-z0-9-]*\b"
)

DBAAS_REALTIME_ACTION_KEYWORDS = {
    "query",
    "show",
    "list",
    "status",
    "detail",
    "inspect",
    "scale",
    "upgrade",
    "restart",
    "create",
    "delete",
    "update",
    "modify",
    "查",
    "查询",
    "查看",
    "列出",
    "状态",
    "详情",
    "资源",
    "扩容",
    "缩容",
    "升级",
    "镜像",
    "重启",
    "创建",
    "删除",
    "更新",
    "修改",
    "cpu",
    "memory",
    "storage",
}

DBAAS_CONCEPT_KEYWORDS = {
    "what is",
    "what's",
    "introduce",
    "explain",
    "definition",
    "concept",
    "介绍",
    "解释",
    "什么是",
    "含义",
    "概念",
    "原理",
    "区别",
    "作用",
}


def classify_dbaas_request(text: str) -> GuardDecision:
    normalized = text.lower()
    has_resource = any(keyword in normalized for keyword in DBAAS_RESOURCE_KEYWORDS) or bool(
        DBAAS_RESOURCE_NAME_PATTERN.search(normalized)
    )
    if not has_resource:
        return "normal"

    has_concept = any(keyword in normalized for keyword in DBAAS_CONCEPT_KEYWORDS)
    has_realtime_action = any(keyword in normalized for keyword in DBAAS_REALTIME_ACTION_KEYWORDS)

    if has_realtime_action:
        return "dbaas_realtime"
    if has_concept:
        return "dbaas_concept"
    return "dbaas_concept"


def build_not_supported_message() -> str:
    return (
        "当前阶段后台还没有接通 mock-server 的查询和操作能力。"
        "现在可以先继续普通问答，或者等下一阶段再接入 DBAAS 服务、主机、集群和任务接口。"
    )
