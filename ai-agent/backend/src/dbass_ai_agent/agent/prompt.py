from __future__ import annotations

from pathlib import Path


DEFAULT_SYSTEM_PROMPT = """你是 DBAAS 智能助手。

当前阶段优先保证：
1. 多用户多 session 的会话体验。
2. 普通问题和 DBAAS 概念问题由真实模型回答。
3. DBAAS 实时查询与操作在工具未接通时明确提示后台尚未启用。
"""


def load_system_prompt(path: Path) -> str:
    return load_prompt(path, DEFAULT_SYSTEM_PROMPT)


def load_compression_prompt(path: Path) -> str:
    return load_prompt(path, DEFAULT_COMPRESSION_PROMPT)


def load_prompt(path: Path, default: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return default


DEFAULT_COMPRESSION_PROMPT = """你是 DBAAS 智能助手的会话压缩器。

你的任务是把历史对话压缩成结构化 JSON。

输出字段固定为：
{
  "current_goal": "",
  "confirmed_facts": [],
  "observed_resources": [],
  "completed_actions": [],
  "approved_actions": [],
  "rejected_actions": [],
  "pending_items": [],
  "constraints": []
}

不要补充不存在的信息。
不要把历史状态写成当前实时状态。
不要输出 JSON 之外的任何文字。
"""
