from __future__ import annotations

import json
from pathlib import Path

from .models import ChatMessage


class MessageStore:
    def load(self, path: Path) -> list[ChatMessage]:
        if not path.exists():
            return []
        messages: list[ChatMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            messages.append(ChatMessage.model_validate(json.loads(line)))
        return messages

    def append(self, path: Path, message: ChatMessage) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(message.model_dump(mode="json"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.write("\n")
