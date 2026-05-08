from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel


class _ModelType(Protocol):
    @classmethod
    def model_validate(cls, obj: object) -> object: ...


ModelT = TypeVar("ModelT", bound=BaseModel)


class AppendLogStore:
    def load(self, path: Path, model: type[ModelT]) -> list[ModelT]:
        if not path.exists():
            return []
        records: list[ModelT] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            records.append(model.model_validate(json.loads(line)))
        return records

    def append(self, path: Path, record: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")


def fold_latest_by_id(records: list[ModelT], id_field: str) -> list[ModelT]:
    latest: dict[str, ModelT] = {}
    for record in records:
        record_id = getattr(record, id_field)
        latest[str(record_id)] = record
    return list(latest.values())
