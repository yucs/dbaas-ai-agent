from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request

from dbass_ai_agent.agent.runtime import DemoAgentRuntime
from dbass_ai_agent.config import Settings, get_settings
from dbass_ai_agent.identity.models import Identity
from dbass_ai_agent.identity.resolver import resolve_identity
from dbass_ai_agent.sessions.approval_store import ApprovalStore
from dbass_ai_agent.sessions.index_store import IndexStore
from dbass_ai_agent.sessions.message_store import MessageStore
from dbass_ai_agent.sessions.repository import SessionRepository
from dbass_ai_agent.sessions.service import SessionService
from dbass_ai_agent.sessions.summary_store import SummaryStore
from dbass_ai_agent.sessions.thread_binding import ThreadBinding


def get_current_identity(request: Request) -> Identity:
    return resolve_identity(request)


@lru_cache
def get_session_repository() -> SessionRepository:
    settings = get_settings()
    return SessionRepository(
        data_root=settings.data_root,
        index_store=IndexStore(),
        message_store=MessageStore(),
        summary_store=SummaryStore(),
        approval_store=ApprovalStore(),
    )


@lru_cache
def get_session_service() -> SessionService:
    return SessionService(
        repository=get_session_repository(),
        thread_binding=ThreadBinding(),
    )


@lru_cache
def get_agent_runtime() -> DemoAgentRuntime:
    return DemoAgentRuntime()


def get_app_settings() -> Settings:
    return get_settings()
