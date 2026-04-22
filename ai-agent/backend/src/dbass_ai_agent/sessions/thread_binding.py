from __future__ import annotations

from .models import SessionMeta


class ThreadBinding:
    def get_thread_id(self, meta: SessionMeta) -> str:
        return meta.thread_id
