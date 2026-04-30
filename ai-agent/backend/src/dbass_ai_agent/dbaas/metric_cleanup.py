from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dbass_ai_agent.config import Settings

from .config import DbaasConfig, dbaas_config_from_settings
from .metric_workspace import METRICS_HISTORY_DIR, METRICS_LATEST_DIR
from .sync import delete_if_exists, parse_time, read_meta, utcnow


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MetricCleanupResult:
    expired_pairs: int = 0
    bad_meta: int = 0
    missing_data_meta: int = 0
    orphan_data: int = 0

    @property
    def deleted_total(self) -> int:
        return self.expired_pairs + self.bad_meta + self.missing_data_meta + self.orphan_data


class DbaasMetricCleanupBackground:
    def __init__(self, settings: Settings) -> None:
        self.config = dbaas_config_from_settings(settings)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="dbaas-metric-cleanup")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        logger.info(
            "dbaas metric cleanup started interval_seconds=%s",
            self.config.metric_snapshot_cleanup_interval_seconds,
        )
        try:
            while True:
                try:
                    result = await asyncio.to_thread(cleanup_metric_snapshots, self.config)
                    if result.deleted_total:
                        logger.info("dbaas metric cleanup deleted result=%s", result)
                except Exception:
                    logger.exception("dbaas metric cleanup iteration failed")
                await asyncio.sleep(self.config.metric_snapshot_cleanup_interval_seconds)
        finally:
            logger.info("dbaas metric cleanup stopped")


def cleanup_metric_snapshots(config: DbaasConfig) -> MetricCleanupResult:
    latest = _cleanup_dir(config.workspace_dir / METRICS_LATEST_DIR)
    history = _cleanup_dir(config.workspace_dir / METRICS_HISTORY_DIR)
    return MetricCleanupResult(
        expired_pairs=latest.expired_pairs + history.expired_pairs,
        bad_meta=latest.bad_meta + history.bad_meta,
        missing_data_meta=latest.missing_data_meta + history.missing_data_meta,
        orphan_data=latest.orphan_data + history.orphan_data,
    )


def _cleanup_dir(directory: Path) -> MetricCleanupResult:
    if not directory.exists():
        return MetricCleanupResult()
    expired_pairs = 0
    bad_meta = 0
    missing_data_meta = 0
    orphan_data = 0
    now = utcnow()

    for meta_path in directory.glob("*.meta.json"):
        data_path = _data_path_for_meta(meta_path)
        meta = read_meta(meta_path)
        if meta is None:
            delete_if_exists(meta_path)
            delete_if_exists(data_path)
            bad_meta += 1
            continue
        if not _meta_data_path_valid(meta, data_path):
            delete_if_exists(meta_path)
            delete_if_exists(data_path)
            bad_meta += 1
            continue
        if not data_path.exists():
            delete_if_exists(meta_path)
            missing_data_meta += 1
            continue
        if _is_expired(meta, now=now):
            delete_if_exists(data_path)
            delete_if_exists(meta_path)
            expired_pairs += 1

    for data_path in directory.glob("*.json"):
        if data_path.name.endswith(".meta.json"):
            continue
        meta_path = _meta_path_for_data(data_path)
        if not meta_path.exists():
            delete_if_exists(data_path)
            orphan_data += 1

    return MetricCleanupResult(
        expired_pairs=expired_pairs,
        bad_meta=bad_meta,
        missing_data_meta=missing_data_meta,
        orphan_data=orphan_data,
    )


def _data_path_for_meta(meta_path: Path) -> Path:
    if not meta_path.name.endswith(".meta.json"):
        return meta_path.with_suffix(".json")
    return meta_path.with_name(f"{meta_path.name[:-len('.meta.json')]}.json")


def _meta_path_for_data(data_path: Path) -> Path:
    return data_path.with_name(f"{data_path.stem}.meta.json")


def _meta_data_path_valid(meta: dict[str, Any], data_path: Path) -> bool:
    meta_data_path = meta.get("data_path")
    return isinstance(meta_data_path, str) and meta_data_path == str(data_path)


def _is_expired(meta: dict[str, Any], *, now) -> bool:
    expires_at = meta.get("expires_at")
    if not isinstance(expires_at, str):
        return True
    try:
        return parse_time(expires_at) < now
    except ValueError:
        return True

