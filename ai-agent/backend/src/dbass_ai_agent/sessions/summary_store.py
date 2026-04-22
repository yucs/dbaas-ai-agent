from __future__ import annotations

import json
from pathlib import Path

from .models import SessionSummary


class SummaryStore:
    def load(self, path: Path) -> SessionSummary:
        if not path.exists():
            return SessionSummary()
        return SessionSummary.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path, summary: SessionSummary) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
