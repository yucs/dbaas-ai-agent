from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True, slots=True)
class HeldSessionLock:
    key: str
    lock: threading.Lock


class SessionLockManager:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._run_locks: dict[str, threading.Lock] = {}
        self._file_locks: dict[str, threading.Lock] = {}

    @contextmanager
    def acquire_run_lock(self, session_id: str) -> Iterator[bool]:
        lock = self._lock_for(self._run_locks, session_id)
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    def is_run_locked(self, session_id: str) -> bool:
        lock = self._lock_for(self._run_locks, session_id)
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
            return False
        return True

    @contextmanager
    def file_lock(self, session_root: Path) -> Iterator[None]:
        lock = self._lock_for(self._file_locks, str(session_root))
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def _lock_for(self, locks: dict[str, threading.Lock], key: str) -> threading.Lock:
        with self._guard:
            lock = locks.get(key)
            if lock is None:
                lock = threading.Lock()
                locks[key] = lock
            return lock


session_locks = SessionLockManager()
