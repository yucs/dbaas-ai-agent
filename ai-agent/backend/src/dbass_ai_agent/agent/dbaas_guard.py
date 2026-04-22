from __future__ import annotations


DBAAS_KEYWORDS = {
    "dbaas",
    "mock-server",
    "service",
    "cluster",
    "host",
    "task",
    "服务",
    "集群",
    "主机",
    "资源",
    "扩容",
    "缩容",
    "升级",
    "镜像",
    "cpu",
    "memory",
    "storage",
}


def looks_like_dbaas_question(text: str) -> bool:
    normalized = text.lower()
    return any(keyword in normalized for keyword in DBAAS_KEYWORDS)


def build_not_supported_message() -> str:
    return (
        "当前阶段后台还没有接通 mock-server 的查询和操作能力。"
        "现在可以先继续普通问答，或者等下一阶段再接入 DBAAS 服务、主机、集群和任务接口。"
    )
