from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from dbass_ai_agent.config import Settings

from .config import dbaas_config_from_settings
from .sync import DbaasServiceSynchronizer


logger = logging.getLogger(__name__)


class DbaasBackgroundSync:
    def __init__(self, settings: Settings) -> None:
        self.config = dbaas_config_from_settings(settings)
        self.synchronizer = DbaasServiceSynchronizer(self.config)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="dbaas-background-sync")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        logger.info("dbaas background sync started interval_seconds=%s", self.config.sync_interval_seconds)
        try:
            while True:
                try:
                    await asyncio.to_thread(self.synchronizer.force_refresh_admin_services)
                except Exception:
                    logger.exception("dbaas background sync iteration failed")
                await asyncio.sleep(self.config.sync_interval_seconds)
        finally:
            logger.info("dbaas background sync stopped")
