from __future__ import annotations

import json
from pathlib import Path

from .models import SessionIndexItem


class IndexStore:
    def load(self, path: Path) -> list[SessionIndexItem]:
        if not path.exists():
            return []
        items = json.loads(path.read_text(encoding="utf-8"))
        return [SessionIndexItem.model_validate(item) for item in items]

    def save(self, path: Path, items: list[SessionIndexItem]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = [item.model_dump(mode="json") for item in items]
        path.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
