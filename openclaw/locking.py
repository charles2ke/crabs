"""Cross-process file locking, used to serialise scheduled (cron) runs.

Scheduled invocations (``--once`` from cron, a systemd timer or GitHub
Actions) can overlap when a poll takes longer than the schedule interval.
:class:`FileLock` makes a run take an exclusive lock before it touches the
seen-store so overlapping runs cannot race on the same state file.

Standard library only: ``fcntl`` on POSIX, ``msvcrt`` on Windows, and an
atomic ``O_EXCL`` lock file when neither is available.
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from types import TracebackType

try:  # pragma: no cover - platform dependent
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - platform dependent
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

__all__ = ["FileLock", "LockError"]


class LockError(RuntimeError):
    """Raised when the lock is held by another run and cannot be acquired."""


class FileLock:
    """An exclusive, advisory lock backed by a lock file.

    ``timeout`` is the number of seconds to keep retrying before giving up
    (``0`` means fail immediately, the sensible default for cron runs).
    """

    def __init__(self, path: str | os.PathLike[str], timeout: float = 0.0, poll: float = 0.1) -> None:
        self.path = Path(path)
        self.timeout = max(0.0, float(timeout))
        self.poll = max(0.01, float(poll))
        self._handle: int | None = None

    def acquire(self) -> None:
        """Take the lock, retrying until ``timeout`` elapses."""
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_acquire():
                return
            if time.monotonic() >= deadline:
                raise LockError(f"another Open Claw run holds the lock {self.path}")
            time.sleep(self.poll)

    def release(self) -> None:
        """Release the lock if this instance holds it."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                os.lseek(handle, 0, os.SEEK_SET)
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
        except OSError:  # pragma: no cover - best effort
            pass
        finally:
            os.close(handle)
            if fcntl is None and msvcrt is None:  # pragma: no cover - fallback
                try:
                    self.path.unlink()
                except OSError:
                    pass

    def _try_acquire(self) -> bool:
        if fcntl is None and msvcrt is None:  # pragma: no cover - fallback
            try:
                self._handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError:
                return False
            return True

        handle = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - Windows
                msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            os.close(handle)
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        self._handle = handle
        return True

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
