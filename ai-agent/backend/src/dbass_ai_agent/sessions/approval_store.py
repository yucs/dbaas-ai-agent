from __future__ import annotations

import json
from pathlib import Path

from dbass_ai_agent.sessions.append_log_store import fold_latest_by_id

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

    def load_latest(self, path: Path) -> list[ApprovalRecord]:
        return fold_latest_by_id(self.load(path), "approval_id")

    def append(self, path: Path, approval: ApprovalRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(approval.model_dump(mode="json"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")
