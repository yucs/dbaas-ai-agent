from __future__ import annotations

import re
from pathlib import Path

from dbass_ai_agent.identity.models import Identity

from .config import DbaasConfig
from .metric_models import MetricScope, MetricSnapshotPaths
from .workspace import safe_filename_part


METRICS_LATEST_DIR = "metrics_latest"
METRICS_HISTORY_DIR = "metrics_history"
METRIC_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")


class MetricWorkspaceError(ValueError):
    """Raised when a metric workspace key cannot be represented safely."""


class MetricWorkspace:
    def __init__(self, config: DbaasConfig) -> None:
        self.root = config.workspace_dir

    def admin_dir(self) -> Path:
        return self.root / "admin"

    def users_dir(self) -> Path:
        return self.root / "users"

    def user_dir(self, user: str | None) -> Path:
        return self.users_dir() / safe_filename_part(user)

    def latest_paths(self, metric_key: str, identity: Identity) -> MetricSnapshotPaths:
        validate_metric_key(metric_key)
        scope, user = identity_scope(identity)
        if scope == "admin":
            directory = self.admin_dir() / METRICS_LATEST_DIR
            key = f"admin/{METRICS_LATEST_DIR}/{metric_key}"
        else:
            safe_user = safe_filename_part(user)
            directory = self.user_dir(user) / METRICS_LATEST_DIR
            key = f"users/{safe_user}/{METRICS_LATEST_DIR}/{metric_key}"
        return _paths(directory, metric_key, scope=scope, user=user, key=key)

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
        filename = f"{safe_filename_part(unit_name)}__{metric_key}__{start_ts}__{end_ts}"
        if scope == "admin":
            directory = self.admin_dir() / METRICS_HISTORY_DIR
            key = f"admin/{METRICS_HISTORY_DIR}/{filename}"
        else:
            safe_user = safe_filename_part(user)
            directory = self.user_dir(user) / METRICS_HISTORY_DIR
            key = f"users/{safe_user}/{METRICS_HISTORY_DIR}/{filename}"
        return _paths(directory, filename, scope=scope, user=user, key=key)


def validate_metric_key(metric_key: str) -> None:
    if METRIC_KEY_PATTERN.fullmatch(metric_key) is None:
        raise MetricWorkspaceError(f"invalid metric_key '{metric_key}'")

def identity_scope(identity: Identity) -> tuple[MetricScope, str | None]:
    if identity.role == "admin":
        return "admin", None
    return "user", identity.user


def _paths(
    directory: Path,
    filename: str,
    *,
    scope: MetricScope,
    user: str | None,
    key: str,
) -> MetricSnapshotPaths:
    data_path = directory / f"{filename}.json"
    meta_path = directory / f"{filename}.meta.json"
    return MetricSnapshotPaths(data_path=data_path, meta_path=meta_path, scope=scope, user=user, key=key)
