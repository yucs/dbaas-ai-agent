from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = APP_ROOT / "config.toml"


class ConfigError(RuntimeError):
    """Raised when the app config file is missing or invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "dbass-ai-agent"
    host: str = "127.0.0.1"
    port: int = 8010
    data_root: Path = APP_ROOT / "data" / "users"
    frontend_root: Path = APP_ROOT / "frontend"
    runtime_root: Path = APP_ROOT / "data" / "runtime"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    checkpoint_db: Path = APP_ROOT / "data" / "runtime" / "checkpoints.sqlite"
    context_window: int = 131072
    max_output_tokens: int = 8192
    thinking_enabled: bool | None = None
    system_prompt_path: Path = APP_ROOT / "backend" / "prompts" / "system.md"
    compression_prompt_path: Path = APP_ROOT / "backend" / "prompts" / "compression.md"
    provider_kind: str = "openai_compatible"
    compression_enabled: bool = True
    soft_trigger_tokens: int = 98304
    keep_recent_messages: int = 6
    summary_max_tokens: int = 2048

    @classmethod
    def from_file(cls, path: Path | None = None) -> "Settings":
        config_path = (path or DEFAULT_CONFIG_PATH).resolve()
        config = load_config_file(config_path)
        app = _get_table(config, "app")
        server = _get_table(config, "server")
        paths = _get_table(config, "paths")
        model = _get_table(config, "model")
        compression = _get_table(config, "compression")
        base_dir = config_path.parent

        runtime_root = _resolve_path(
            base_dir,
            _get_string(paths, "runtime_root", "./data/runtime"),
        )
        context_window = _get_int(model, "context_window", 131072)
        max_output_tokens = _get_int(model, "max_output_tokens", 8192)
        return cls(
            app_name=_get_string(app, "name", "dbass-ai-agent"),
            host=_get_string(server, "host", "127.0.0.1"),
            port=_get_int(server, "port", 8010),
            data_root=_resolve_path(
                base_dir,
                _get_string(paths, "data_root", "./data/users"),
            ),
            frontend_root=_resolve_path(
                base_dir,
                _get_string(paths, "frontend_root", "./frontend"),
            ),
            runtime_root=runtime_root,
            model=_get_optional_string(model, "model"),
            base_url=_get_optional_string(model, "base_url"),
            api_key=_get_optional_string(model, "api_key"),
            checkpoint_db=_resolve_path(
                base_dir,
                _get_string(paths, "checkpoint_db", "./data/runtime/checkpoints.sqlite"),
            ),
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            thinking_enabled=_get_optional_bool(model, "thinking_enabled"),
            system_prompt_path=_resolve_path(
                base_dir,
                _get_string(paths, "system_prompt_path", "./backend/prompts/system.md"),
            ),
            compression_prompt_path=_resolve_path(
                base_dir,
                _get_string(paths, "compression_prompt_path", "./backend/prompts/compression.md"),
            ),
            provider_kind=_get_string(model, "provider_kind", "openai_compatible"),
            compression_enabled=_get_bool(compression, "enabled", True),
            soft_trigger_tokens=_get_int(
                compression,
                "soft_trigger_tokens",
                max(4096, int(context_window * 0.75)),
            ),
            keep_recent_messages=_get_int(compression, "keep_recent_messages", 6),
            summary_max_tokens=_get_int(compression, "summary_max_tokens", 2048),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_file()


def load_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"缺少配置文件: {path}。请先从 config.example.toml 复制一份为 config.toml。"
        )
    try:
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件解析失败: {path}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"配置文件格式无效: {path}")
    return loaded


def _get_table(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置段 `{name}` 必须是对象。")
    return value


def _get_string(config: Mapping[str, Any], key: str, default: str) -> str:
    value = config.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"配置项 `{key}` 必须是字符串。")
    stripped = value.strip()
    return stripped or default


def _get_optional_string(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"配置项 `{key}` 必须是字符串。")
    stripped = value.strip()
    return stripped or None


def _get_int(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"配置项 `{key}` 必须是整数。")
    return value


def _get_bool(config: Mapping[str, Any], key: str, default: bool) -> bool:
    value = config.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"配置项 `{key}` 必须是布尔值。")
    return value


def _get_optional_bool(config: Mapping[str, Any], key: str) -> bool | None:
    value = config.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ConfigError(f"配置项 `{key}` 必须是布尔值。")
    return value


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()
