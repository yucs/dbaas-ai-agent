from __future__ import annotations

import re

from fastapi import HTTPException, status

from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.infra.clock import utc_now
from dbass_ai_agent.infra.ids import new_message_id, new_session_thread_ids

from .models import ChatMessage, SessionDetail, SessionIndexItem, SessionMeta
from .repository import SessionRepository
from .thread_binding import ThreadBinding


DEFAULT_TITLE = "新对话"
SAFE_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SessionService:
    def __init__(self, repository: SessionRepository, thread_binding: ThreadBinding) -> None:
        self.repository = repository
        self.thread_binding = thread_binding

    def list_sessions(self, identity: Identity) -> list[SessionIndexItem]:
        return self.repository.list_index(identity.user_id)

    def create_session(
        self,
        identity: Identity,
        *,
        title: str | None,
    ) -> SessionDetail:
        now = utc_now()
        session_id, thread_id = new_session_thread_ids(identity.user_id)
        meta = SessionMeta(
            session_id=session_id,
            user_id=identity.user_id,
            role=identity.role,
            user=identity.user,
            thread_id=thread_id,
            title=(title or DEFAULT_TITLE).strip() or DEFAULT_TITLE,
            status="active",
            created_at=now,
            updated_at=now,
        )
        self.repository.save_meta(meta)
        self.repository.upsert_index_item(
            meta.user_id,
            SessionIndexItem(
                session_id=meta.session_id,
                title=meta.title,
                status=meta.status,
                updated_at=meta.updated_at,
                last_message_at=meta.last_message_at,
                preview="",
            ),
        )
        return self.repository.load_detail(meta.user_id, meta.session_id)

    def get_session(self, identity: Identity, session_id: str) -> SessionDetail:
        self._assert_safe_session_id(session_id)
        try:
            detail = self.repository.load_detail(identity.user_id, session_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session 不存在。",
            ) from exc
        if detail.meta.status == "deleted":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session 不存在。")
        self._assert_identity(detail.meta, identity)
        return detail

    def archive_session(self, identity: Identity, session_id: str) -> SessionDetail:
        detail = self.get_session(identity, session_id)
        now = utc_now()
        detail.meta.status = "archived"
        detail.meta.archived_at = now
        detail.meta.updated_at = now
        self._save_meta_and_index(detail.meta, preview=self._latest_preview(detail.messages))
        return self.repository.load_detail(identity.user_id, session_id)

    def restore_session(self, identity: Identity, session_id: str) -> SessionDetail:
        detail = self.get_session(identity, session_id)
        now = utc_now()
        detail.meta.status = "active"
        detail.meta.archived_at = None
        detail.meta.updated_at = now
        self._save_meta_and_index(detail.meta, preview=self._latest_preview(detail.messages))
        return self.repository.load_detail(identity.user_id, session_id)

    def delete_session(self, identity: Identity, session_id: str) -> str:
        detail = self.get_session(identity, session_id)
        self.repository.remove_index_item(detail.meta.user_id, detail.meta.session_id)
        self.repository.delete_session_directory(detail.meta.user_id, detail.meta.session_id)
        return detail.meta.session_id

    def ensure_active_session(self, identity: Identity, session_id: str) -> SessionMeta:
        detail = self.get_session(identity, session_id)
        if detail.meta.status == "archived":
            detail = self.restore_session(identity, session_id)
        return detail.meta

    def append_user_message(self, identity: Identity, session_id: str, content: str) -> ChatMessage:
        meta = self.ensure_active_session(identity, session_id)
        now = utc_now()
        message = ChatMessage(
            message_id=new_message_id(),
            role="user",
            content=content,
            created_at=now,
        )
        self.repository.append_message(meta.user_id, meta.session_id, message)
        if meta.title == DEFAULT_TITLE:
            meta.title = self._build_title_from_message(content)
        meta.updated_at = now
        meta.last_message_at = now
        self._save_meta_and_index(meta, preview=content)
        return message

    def append_assistant_message(self, identity: Identity, session_id: str, content: str) -> ChatMessage:
        meta = self.ensure_active_session(identity, session_id)
        now = utc_now()
        message = ChatMessage(
            message_id=new_message_id(),
            role="assistant",
            content=content,
            created_at=now,
        )
        self.repository.append_message(meta.user_id, meta.session_id, message)
        meta.updated_at = now
        meta.last_message_at = now
        self._save_meta_and_index(meta, preview=content)
        return message

    def append_ai_agent_message(
        self,
        identity: Identity,
        session_id: str,
        content: str,
    ) -> ChatMessage:
        meta = self.ensure_active_session(identity, session_id)
        now = utc_now()
        message = ChatMessage(
            message_id=new_message_id(),
            role="ai-agent",
            content=content,
            created_at=now,
        )
        self.repository.append_message(meta.user_id, meta.session_id, message)
        meta.updated_at = now
        meta.last_message_at = now
        self._save_meta_and_index(meta, preview=content)
        return message

    def get_messages(self, identity: Identity, session_id: str) -> list[ChatMessage]:
        meta = self.get_session(identity, session_id).meta
        return self.repository.load_messages(meta.user_id, meta.session_id)

    def get_thread_id(self, identity: Identity, session_id: str) -> str:
        return self.thread_binding.get_thread_id(self.get_session(identity, session_id).meta)

    def _save_meta_and_index(self, meta: SessionMeta, *, preview: str) -> None:
        self.repository.save_meta(meta)
        self.repository.upsert_index_item(
            meta.user_id,
            SessionIndexItem(
                session_id=meta.session_id,
                title=meta.title,
                status=meta.status,
                updated_at=meta.updated_at,
                last_message_at=meta.last_message_at,
                preview=self._truncate_preview(preview),
            ),
        )

    @staticmethod
    def _build_title_from_message(content: str) -> str:
        compact = " ".join(content.strip().split())
        if not compact:
            return DEFAULT_TITLE
        return compact[:24]

    @staticmethod
    def _truncate_preview(content: str) -> str:
        compact = " ".join(content.strip().split())
        return compact[:80]

    @staticmethod
    def _latest_preview(messages: list[ChatMessage]) -> str:
        if not messages:
            return ""
        return messages[-1].content

    @staticmethod
    def _assert_identity(meta: SessionMeta, identity: Identity) -> None:
        if meta.user_id != identity.user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session 不存在。")

    @staticmethod
    def _assert_safe_session_id(session_id: str) -> None:
        if not SAFE_SESSION_ID_PATTERN.fullmatch(session_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session 不存在。")
