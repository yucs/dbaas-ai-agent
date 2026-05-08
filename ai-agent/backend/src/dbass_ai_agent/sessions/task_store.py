from __future__ import annotations

from pathlib import Path

from dbass_ai_agent.operations.models import TaskRecord

from .append_log_store import AppendLogStore, fold_latest_by_id


class TaskStore:
    def __init__(self) -> None:
        self._append_log = AppendLogStore()

    def load(self, path: Path) -> list[TaskRecord]:
        return self._append_log.load(path, TaskRecord)

    def load_latest(self, path: Path) -> list[TaskRecord]:
        return fold_latest_by_id(self.load(path), "task_id")

    def append(self, path: Path, task: TaskRecord) -> None:
        self._append_log.append(path, task)
