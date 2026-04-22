from __future__ import annotations

import json
from pathlib import Path

from .models import ApprovalRecord


class ApprovalStore:
    def load(self, path: Path) -> list[ApprovalRecord]:
        if not path.exists():
            return []
        approvals: list[ApprovalRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            approvals.append(ApprovalRecord.model_validate(json.loads(line)))
        return approvals
