from __future__ import annotations

from pathlib import Path

from dbass_ai_agent.identity.models import UserRole


def load_system_prompt(path: Path, role: UserRole) -> str:
    common = load_required_prompt(path)
    extend = load_required_prompt(role_extend_system_prompt_path(path, role))
    return f"{common}\n\n{extend}".strip()


def load_compression_prompt(path: Path) -> str:
    return load_required_prompt(path)


def load_required_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"缺少必需提示词文件：{path}")
    return path.read_text(encoding="utf-8").strip()


def role_extend_system_prompt_path(system_prompt_path: Path, role: UserRole) -> Path:
    filename = "admin_extend_system_prompt.md" if role == "admin" else "user_extend_system_prompt.md"
    return system_prompt_path.parent / filename
