from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dbass_ai_agent.config import Settings


@dataclass(frozen=True, slots=True)
class DbaasConfig:
    server_base_url: str
    request_timeout_seconds: int
    workspace_dir: Path
    service_sync_interval_seconds: int
    service_snapshot_ttl_seconds: int
    backup_snapshot_ttl_seconds: int
    user_active_idle_timeout_seconds: int
    user_snapshot_refresh_wait_seconds: int
    jq_timeout_seconds: int
    jq_max_preview_items: int
    jq_max_output_bytes: int
    metric_snapshot_ttl_seconds: int
    metric_snapshot_cleanup_interval_seconds: int
    metric_refresh_lock_timeout_seconds: int
    host_sync_interval_seconds: int = 60
    host_snapshot_ttl_seconds: int = 120
    host_refresh_lock_timeout_seconds: int = 10


def dbaas_config_from_settings(settings: Settings) -> DbaasConfig:
    return DbaasConfig(
        server_base_url=settings.dbaas_server_base_url.rstrip("/"),
        request_timeout_seconds=settings.dbaas_request_timeout_seconds,
        workspace_dir=settings.dbaas_workspace_dir,
        service_sync_interval_seconds=settings.dbaas_service_sync_interval_seconds,
        service_snapshot_ttl_seconds=settings.dbaas_service_snapshot_ttl_seconds,
        backup_snapshot_ttl_seconds=settings.dbaas_backup_snapshot_ttl_seconds,
        user_active_idle_timeout_seconds=settings.dbaas_user_active_idle_timeout_seconds,
        user_snapshot_refresh_wait_seconds=settings.dbaas_user_snapshot_refresh_wait_seconds,
        jq_timeout_seconds=settings.dbaas_jq_timeout_seconds,
        jq_max_preview_items=settings.dbaas_jq_max_preview_items,
        jq_max_output_bytes=settings.dbaas_jq_max_output_bytes,
        metric_snapshot_ttl_seconds=settings.dbaas_metric_snapshot_ttl_seconds,
        metric_snapshot_cleanup_interval_seconds=settings.dbaas_metric_snapshot_cleanup_interval_seconds,
        metric_refresh_lock_timeout_seconds=settings.dbaas_metric_refresh_lock_timeout_seconds,
        host_sync_interval_seconds=settings.dbaas_host_sync_interval_seconds,
        host_snapshot_ttl_seconds=settings.dbaas_host_snapshot_ttl_seconds,
        host_refresh_lock_timeout_seconds=settings.dbaas_host_refresh_lock_timeout_seconds,
    )
