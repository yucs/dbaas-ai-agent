from __future__ import annotations

import re
from pathlib import Path

from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .metric_models import MetricScope, MetricSnapshotPaths


METRICS_LATEST_DIR = "metrics_latest"
METRICS_HISTORY_DIR = "metrics_history"
METRIC_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")
SAFE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


class MetricWorkspaceError(ValueError):
    """Raised when a metric workspace key cannot be represented safely."""


class MetricWorkspace:
    def __init__(self, config: DbaasConfig) -> None:
        self.root = config.workspace_dir

    def latest_paths(self, metric_key: str, identity: Identity) -> MetricSnapshotPaths:
        validate_metric_key(metric_key)
        latest_dir = self.root / METRICS_LATEST_DIR
        scope, user = identity_scope(identity)
        if scope == "admin":
            key = metric_key
        else:
            key = f"user__{safe_filename_part(user)}__{metric_key}"
        return _paths(latest_dir, key, scope=scope, user=user)

    def history_paths(
        self,
        *,
        unit_name: str,
        metric_key: str,
        start_ts: int,
        end_ts: int,
        identity: Identity,
    ) -> MetricSnapshotPaths:
        validate_metric_key(metric_key)
        scope, user = identity_scope(identity)
        safe_user = "all" if scope == "admin" else safe_filename_part(user)
        key = (
            f"{scope}__{safe_user}__{safe_filename_part(unit_name)}__"
            f"{metric_key}__{start_ts}__{end_ts}"
        )
        return _paths(self.root / METRICS_HISTORY_DIR, key, scope=scope, user=user)


def validate_metric_key(metric_key: str) -> None:
    if METRIC_KEY_PATTERN.fullmatch(metric_key) is None:
        raise MetricWorkspaceError(f"invalid metric_key '{metric_key}'")


def safe_filename_part(value: str | None, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    safe = SAFE_FILENAME_PATTERN.sub("_", value.strip())
    return safe or fallback


def identity_scope(identity: Identity) -> tuple[MetricScope, str | None]:
    if identity.role == "admin":
        return "admin", None
    return "user", identity.user


def _paths(
    directory: Path,
    key: str,
    *,
    scope: MetricScope,
    user: str | None,
) -> MetricSnapshotPaths:
    data_path = directory / f"{key}.json"
    meta_path = directory / f"{key}.meta.json"
    return MetricSnapshotPaths(data_path=data_path, meta_path=meta_path, scope=scope, user=user, key=key)
