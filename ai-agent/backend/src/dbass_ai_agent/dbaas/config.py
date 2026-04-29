from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dbass_ai_agent.config import Settings


@dataclass(frozen=True, slots=True)
class DbaasConfig:
    server_base_url: str
    request_timeout_seconds: int
    workspace_dir: Path
    sync_interval_seconds: int
    ttl_seconds: int
    jq_timeout_seconds: int
    jq_max_preview_items: int
    jq_max_output_bytes: int


def dbaas_config_from_settings(settings: Settings) -> DbaasConfig:
    return DbaasConfig(
        server_base_url=settings.dbaas_server_base_url.rstrip("/"),
        request_timeout_seconds=settings.dbaas_request_timeout_seconds,
        workspace_dir=settings.dbaas_workspace_dir,
        sync_interval_seconds=settings.dbaas_sync_interval_seconds,
        ttl_seconds=settings.dbaas_ttl_seconds,
        jq_timeout_seconds=settings.dbaas_jq_timeout_seconds,
        jq_max_preview_items=settings.dbaas_jq_max_preview_items,
        jq_max_output_bytes=settings.dbaas_jq_max_output_bytes,
    )
