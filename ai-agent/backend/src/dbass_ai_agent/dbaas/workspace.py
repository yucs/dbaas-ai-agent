from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DbaasConfig
from .constants import (
    ADMIN_SCOPE,
    DATA_FILE_NAMES,
    META_FILE_NAMES,
    USER_SCOPE,
)


SAFE_FILENAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True, slots=True)
class DbaasSnapshotPaths:
    data_path: Path
    meta_path: Path
    scope: str
    user: str | None
    key: str


class DbaasWorkspace:
    def __init__(self, config: DbaasConfig) -> None:
        self.config = config
        self.root = config.workspace_dir

    def admin_dir(self) -> Path:
        return self.root / ADMIN_SCOPE

    def users_dir(self) -> Path:
        return self.root / "users"

    def user_dir(self, user: str | None) -> Path:
        return self.users_dir() / safe_filename_part(user)

    def paths(self, kind: str, *, scope: str, user: str | None = None) -> DbaasSnapshotPaths:
        if scope == ADMIN_SCOPE:
            directory = self.admin_dir()
            key = ADMIN_SCOPE
            path_user = None
        elif scope == USER_SCOPE:
            safe_user = safe_filename_part(user)
            directory = self.users_dir() / safe_user
            key = f"{USER_SCOPE}/{safe_user}"
            path_user = user
        else:
            raise ValueError(f"unsupported dbaas snapshot scope: {scope}")
        return DbaasSnapshotPaths(
            data_path=directory / DATA_FILE_NAMES[kind],
            meta_path=directory / META_FILE_NAMES[kind],
            scope=scope,
            user=path_user,
            key=key,
        )

    def data_path(self, kind: str) -> Path:
        return self.paths(kind, scope=ADMIN_SCOPE).data_path

    def meta_path(self, kind: str) -> Path:
        return self.paths(kind, scope=ADMIN_SCOPE).meta_path

    def user_data_path(self, kind: str, user: str | None) -> Path:
        return self.paths(kind, scope=USER_SCOPE, user=user).data_path

    def user_meta_path(self, kind: str, user: str | None) -> Path:
        return self.paths(kind, scope=USER_SCOPE, user=user).meta_path

    def cleanup_orphan_temp_files(self, *, min_age_seconds: int = 60) -> int:
        if not self.root.exists():
            return 0
        now = datetime.now(tz=UTC)
        deleted = 0
        for pattern in (".services.json.*.tmp", ".services.meta.json.*.tmp"):
            for path in self.root.rglob(pattern):
                try:
                    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                    if now - modified_at < timedelta(seconds=min_age_seconds):
                        continue
                    path.unlink()
                    deleted += 1
                except OSError:
                    continue
        return deleted


def safe_filename_part(value: str | None, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    safe = SAFE_FILENAME_PATTERN.sub("_", value.strip())
    return safe or fallback


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_temp(path: Path, payload: Any) -> tuple[Path, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return tmp_path, tmp_path.stat().st_size
    except Exception:
        if tmp_path is not None:
            delete_if_exists(tmp_path)
        raise


def replace_file_atomic(source_path: Path, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source_path, path)
    return path.stat().st_size


def write_json_atomic(path: Path, payload: Any) -> int:
    tmp_path, _ = write_json_temp(path, payload)
    os.replace(tmp_path, path)
    return path.stat().st_size


def write_meta_atomic(path: Path, meta: Mapping[str, Any]) -> int:
    return write_json_atomic(path, dict(meta))


def delete_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
