from __future__ import annotations

import os
import time
from pathlib import Path
from types import TracebackType


class ResourceLockTimeoutError(TimeoutError):
    """Raised when waiting for a DBAAS resource lock times out."""


class ResourceFileLock:
    def __init__(self, path: Path, *, timeout_seconds: int) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def __enter__(self) -> "ResourceFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if self._remove_stale_lock():
                    continue
                if time.monotonic() >= deadline:
                    raise ResourceLockTimeoutError(f"resource lock timeout: {self.path}") from None
                time.sleep(0.05)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _remove_stale_lock(self) -> bool:
        try:
            raw_pid = self.path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return True
        except OSError:
            return False

        try:
            pid = int(raw_pid)
        except ValueError:
            return False

        if pid <= 0 or _pid_exists(pid):
            return False

        try:
            if self.path.read_text(encoding="ascii").strip() != raw_pid:
                return False
            self.path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False


def lock_exists(path: Path) -> bool:
    return path.exists()


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
