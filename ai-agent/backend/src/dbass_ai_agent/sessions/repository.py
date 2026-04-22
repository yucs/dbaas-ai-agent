from __future__ import annotations

import json
import shutil
from pathlib import Path

from dbass_ai_agent.infra.paths import build_session_paths, build_user_sessions_root

from .approval_store import ApprovalStore
from .index_store import IndexStore
from .message_store import MessageStore
from .models import (
    ApprovalRecord,
    ChatMessage,
    SessionDetail,
    SessionIndexItem,
    SessionMeta,
    SessionSummary,
)
from .summary_store import SummaryStore


class SessionRepository:
    def __init__(
        self,
        data_root: Path,
        index_store: IndexStore,
        message_store: MessageStore,
        summary_store: SummaryStore,
        approval_store: ApprovalStore,
    ) -> None:
        self.data_root = data_root
        self.index_store = index_store
        self.message_store = message_store
        self.summary_store = summary_store
        self.approval_store = approval_store

    def list_index(self, user_id: str) -> list[SessionIndexItem]:
        sessions_root = build_user_sessions_root(self.data_root, user_id)
        items = self.index_store.load(sessions_root / "index.json")
        return sorted(
            [item for item in items if item.status != "deleted"],
            key=lambda item: item.last_message_at or item.updated_at,
            reverse=True,
        )

    def load_meta(self, user_id: str, session_id: str) -> SessionMeta:
        paths = build_session_paths(self.data_root, user_id, session_id)
        if not paths.meta_path.exists():
            raise FileNotFoundError(session_id)
        return SessionMeta.model_validate(json.loads(paths.meta_path.read_text(encoding="utf-8")))

    def save_meta(self, meta: SessionMeta) -> None:
        paths = build_session_paths(self.data_root, meta.user_id, meta.session_id)
        paths.session_root.mkdir(parents=True, exist_ok=True)
        paths.meta_path.write_text(
            json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_messages(self, user_id: str, session_id: str) -> list[ChatMessage]:
        return self.message_store.load(build_session_paths(self.data_root, user_id, session_id).messages_path)

    def append_message(self, user_id: str, session_id: str, message: ChatMessage) -> None:
        self.message_store.append(
            build_session_paths(self.data_root, user_id, session_id).messages_path,
            message,
        )

    def load_summary(self, user_id: str, session_id: str) -> SessionSummary:
        return self.summary_store.load(build_session_paths(self.data_root, user_id, session_id).summary_path)

    def save_summary(self, user_id: str, session_id: str, summary: SessionSummary) -> None:
        self.summary_store.save(
            build_session_paths(self.data_root, user_id, session_id).summary_path,
            summary,
        )

    def load_approvals(self, user_id: str, session_id: str) -> list[ApprovalRecord]:
        return self.approval_store.load(
            build_session_paths(self.data_root, user_id, session_id).approvals_path,
        )

    def load_detail(self, user_id: str, session_id: str) -> SessionDetail:
        return SessionDetail(
            meta=self.load_meta(user_id, session_id),
            messages=self.load_messages(user_id, session_id),
            summary=self.load_summary(user_id, session_id),
            approvals=self.load_approvals(user_id, session_id),
        )

    def upsert_index_item(self, user_id: str, item: SessionIndexItem) -> None:
        sessions_root = build_user_sessions_root(self.data_root, user_id)
        index_path = sessions_root / "index.json"
        current = self.index_store.load(index_path)
        replaced = False
        new_items: list[SessionIndexItem] = []
        for existing in current:
            if existing.session_id == item.session_id:
                new_items.append(item)
                replaced = True
            else:
                new_items.append(existing)
        if not replaced:
            new_items.append(item)
        ordered = sorted(
            new_items,
            key=lambda index_item: index_item.last_message_at or index_item.updated_at,
            reverse=True,
        )
        self.index_store.save(index_path, ordered)

    def remove_index_item(self, user_id: str, session_id: str) -> None:
        sessions_root = build_user_sessions_root(self.data_root, user_id)
        index_path = sessions_root / "index.json"
        current = self.index_store.load(index_path)
        kept_items = [item for item in current if item.session_id != session_id]
        ordered = sorted(
            kept_items,
            key=lambda index_item: index_item.last_message_at or index_item.updated_at,
            reverse=True,
        )
        self.index_store.save(index_path, ordered)

    def delete_session_directory(self, user_id: str, session_id: str) -> None:
        session_root = build_session_paths(self.data_root, user_id, session_id).session_root
        if session_root.exists():
            shutil.rmtree(session_root)
