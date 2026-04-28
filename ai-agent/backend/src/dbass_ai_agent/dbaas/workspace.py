from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import DbaasConfig
from .constants import (
    ADMIN_SCOPE,
    DATA_FILE_NAMES,
    LOCK_FILE_NAMES,
    META_FILE_NAMES,
    SCHEMA_FILES,
    USERS_SCOPE,
)


SAFE_WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class WorkspaceError(RuntimeError):
    """Raised when DBAAS workspace paths or files are invalid."""


def ensure_safe_workspace_id(value: str) -> str:
    if not SAFE_WORKSPACE_ID_PATTERN.fullmatch(value):
        raise WorkspaceError("workspace id contains unsafe characters")
    return value


class DbaasWorkspace:
    def __init__(self, config: DbaasConfig) -> None:
        self.config = config
        self.root = config.workspace_dir

    def admin_dir(self) -> Path:
        return self.root / ADMIN_SCOPE

    def user_dir(self, user_id: str) -> Path:
        return self.root / USERS_SCOPE / ensure_safe_workspace_id(user_id)

    def data_path(self, kind: str, *, user_id: str | None = None) -> Path:
        return self.scope_dir(user_id=user_id) / DATA_FILE_NAMES[kind]

    def meta_path(self, kind: str, *, user_id: str | None = None) -> Path:
        return self.scope_dir(user_id=user_id) / META_FILE_NAMES[kind]

    def lock_path(self, kind: str, *, user_id: str | None = None) -> Path:
        return self.scope_dir(user_id=user_id) / LOCK_FILE_NAMES[kind]

    def schema_path(self, kind: str) -> Path:
        return (Path.cwd() / SCHEMA_FILES[kind]).resolve()

    def scope_dir(self, *, user_id: str | None = None) -> Path:
        if user_id is None:
            return self.admin_dir()
        return self.user_dir(user_id)

    def ensure_scope_dir(self, *, user_id: str | None = None) -> Path:
        path = self.scope_dir(user_id=user_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_atomic(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    os.replace(tmp_path, path)
    return path.stat().st_size


def write_meta_atomic(path: Path, meta: Mapping[str, Any]) -> int:
    return write_json_atomic(path, dict(meta))


def delete_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
