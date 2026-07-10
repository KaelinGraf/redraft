"""Write-lock factory (design §6.2).

filelock.FileLock (kernel-backed fcntl.flock on POSIX, not SoftFileLock) is used specifically
because the OS releases these locks automatically when the holding process's file descriptors
close, including on a crash or kill -9 — no manual stale-lock cleanup needed for crash recovery.
The lock file lives at a fixed sibling path outside index/ (never deleted by any operation,
including reindex()'s directory-rebuild), so a delete-and-recreate of index/ can never orphan a
held lock onto a stale inode.
"""

from __future__ import annotations

from pathlib import Path

import filelock

from redraft.config import GraphPaths
from redraft.errors import LockTimeoutError

LOCK_TIMEOUT_SECONDS = 30.0


class _TranslatingFileLock(filelock.FileLock):
    """A filelock.FileLock whose acquire (including via `with`) raises this package's own
    LockTimeoutError instead of filelock.Timeout, so callers only depend on errors.py."""

    def acquire(self, *args: object, **kwargs: object) -> filelock.AcquireReturnProxy:
        try:
            return super().acquire(*args, **kwargs)
        except filelock.Timeout as e:
            raise LockTimeoutError(
                f"could not acquire write lock {self.lock_file!r} within {self.timeout}s"
            ) from e


def write_lock(graph_dir: Path) -> filelock.FileLock:
    """A fresh lock object for the store's single write lock."""
    return _TranslatingFileLock(GraphPaths(graph_dir).lock_file, timeout=LOCK_TIMEOUT_SECONDS)
