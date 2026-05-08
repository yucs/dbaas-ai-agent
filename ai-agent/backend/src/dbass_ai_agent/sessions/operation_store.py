from __future__ import annotations

from pathlib import Path

from dbass_ai_agent.operations.models import OperationRecord

from .append_log_store import AppendLogStore, fold_latest_by_id


class OperationStore:
    def __init__(self) -> None:
        self._append_log = AppendLogStore()

    def load(self, path: Path) -> list[OperationRecord]:
        return self._append_log.load(path, OperationRecord)

    def load_latest(self, path: Path) -> list[OperationRecord]:
        return fold_latest_by_id(self.load(path), "operation_id")

    def append(self, path: Path, operation: OperationRecord) -> None:
        self._append_log.append(path, operation)
