from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import DbaasConfig
from .constants import (
    ADMIN_SCOPE,
    DATA_FILE_NAMES,
    META_FILE_NAMES,
)


class DbaasWorkspace:
    def __init__(self, config: DbaasConfig) -> None:
        self.config = config
        self.root = config.workspace_dir

    def admin_dir(self) -> Path:
        return self.root / ADMIN_SCOPE

    def data_path(self, kind: str) -> Path:
        return self.admin_dir() / DATA_FILE_NAMES[kind]

    def meta_path(self, kind: str) -> Path:
        return self.admin_dir() / META_FILE_NAMES[kind]


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_temp(path: Path, payload: Any) -> tuple[Path, int]:
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
    return tmp_path, tmp_path.stat().st_size


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
