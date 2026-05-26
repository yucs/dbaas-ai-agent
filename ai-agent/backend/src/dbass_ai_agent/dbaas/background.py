from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import suppress
from datetime import UTC, datetime

from dbass_ai_agent.config import Settings
from dbass_ai_agent.identity.models import Identity

from .config import dbaas_config_from_settings
from .sync import DbaasServiceSynchronizer
from .workspace import DbaasWorkspace


logger = logging.getLogger(__name__)


class DbaasBackgroundSync:
    def __init__(self, settings: Settings) -> None:
        self.config = dbaas_config_from_settings(settings)
        self.synchronizer = DbaasServiceSynchronizer(self.config)
        self.workspace = DbaasWorkspace(self.config)
        self._admin_task: asyncio.Task[None] | None = None
        self._user_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._leases: dict[str, datetime] = {}
        self._leases_guard = threading.Lock()

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._admin_task is not None and not self._admin_task.done():
            return
        self._admin_task = asyncio.create_task(self._run_admin(), name="dbaas-background-sync")
        self._user_task = asyncio.create_task(self._run_users(), name="dbaas-user-services-sync")

    async def stop(self) -> None:
        for task in (self._admin_task, self._user_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._admin_task = None
        self._user_task = None
        self._loop = None

    def renew_user_lease(self, identity: Identity) -> None:
        if identity.role == "admin" or not identity.user:
            return
        user = identity.user
        with self._leases_guard:
            self._leases[user] = _utcnow()
        loop = self._loop
        if loop is None or not loop.is_running():
            return
        loop.call_soon_threadsafe(lambda: asyncio.create_task(self._prewarm_user(user)))

    async def _run_admin(self) -> None:
        logger.info("dbaas background sync started interval_seconds=%s", self.config.sync_interval_seconds)
        try:
            deleted = await asyncio.to_thread(self.workspace.cleanup_orphan_temp_files)
            if deleted:
                logger.info("dbaas workspace orphan temp files cleaned count=%s", deleted)
            while True:
                try:
                    await asyncio.to_thread(self.synchronizer.force_refresh_admin_services)
                except Exception:
                    logger.exception("dbaas background sync iteration failed")
                await asyncio.sleep(self.config.sync_interval_seconds)
        finally:
            logger.info("dbaas background sync stopped")

    async def _run_users(self) -> None:
        logger.info("dbaas user services sync started interval_seconds=%s", self.config.sync_interval_seconds)
        try:
            while True:
                await asyncio.sleep(self.config.sync_interval_seconds)
                active_users, expired_users = self._active_and_expired_users()
                for user in expired_users:
                    try:
                        await asyncio.to_thread(self.synchronizer.delete_user_services_snapshot, user)
                    except Exception:
                        logger.exception("dbaas user services snapshot delete failed user=%s", user)
                for user in active_users:
                    try:
                        await asyncio.to_thread(
                            self.synchronizer.force_refresh_user_services,
                            user,
                            timeout_seconds=0,
                        )
                    except Exception:
                        logger.exception("dbaas user services sync failed user=%s", user)
        finally:
            logger.info("dbaas user services sync stopped")

    async def _prewarm_user(self, user: str) -> None:
        try:
            await asyncio.to_thread(
                self.synchronizer.force_refresh_user_services,
                user,
                timeout_seconds=self.config.user_snapshot_refresh_wait_seconds,
            )
        except Exception:
            logger.exception("dbaas user services prewarm failed user=%s", user)

    def _active_and_expired_users(self) -> tuple[list[str], list[str]]:
        now = _utcnow()
        active_users: list[str] = []
        expired_users: list[str] = []
        with self._leases_guard:
            for user, renewed_at in list(self._leases.items()):
                idle_seconds = (now - renewed_at).total_seconds()
                if idle_seconds > self.config.user_active_idle_timeout_seconds:
                    expired_users.append(user)
                    self._leases.pop(user, None)
                else:
                    active_users.append(user)
        return active_users, expired_users


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)
