"""Thread-safe repository status cache.

One RepoStatus per repo root, replaced atomically on refresh. Reads never
block on a mutex for longer than a dict swap, so UI-thread lookups (once per
visible row, potentially thousands of times per repaint) stay cheap even
while a refresh is in flight on a worker thread.
"""

from __future__ import annotations

from PyQt6.QtCore import QMutex, QMutexLocker

from src.vcs.status import FileStatus, RepoStatus


class RepoStatusCache:
    """Owned by VcsManager. Not a singleton -- VcsManager owns exactly one,
    which is enough since it's already process-wide.
    """

    def __init__(self):
        self._mutex = QMutex()
        self._by_root: dict[str, RepoStatus] = {}
        self._generation: dict[str, int] = {}

    def next_generation(self, repo_root: str) -> int:
        """Reserve the next generation number for repo_root before starting
        a refresh, so a slow, superseded refresh can be detected and
        dropped when it finishes (see VcsManager._on_status_ready).
        """
        with QMutexLocker(self._mutex):
            gen = self._generation.get(repo_root, 0) + 1
            self._generation[repo_root] = gen
            return gen

    def current_generation(self, repo_root: str) -> int:
        with QMutexLocker(self._mutex):
            return self._generation.get(repo_root, 0)

    def store(self, status: RepoStatus) -> bool:
        """Install *status* if it's not superseded by a newer refresh already
        in flight. Returns True if it was installed.
        """
        with QMutexLocker(self._mutex):
            if status.generation < self._generation.get(status.repo_root, 0):
                return False
            self._by_root[status.repo_root] = status
            return True

    def get(self, repo_root: str) -> RepoStatus | None:
        with QMutexLocker(self._mutex):
            return self._by_root.get(repo_root)

    def status_for_path(self, repo_root: str, relative_path: str) -> FileStatus:
        status = self.get(repo_root)
        if status is None:
            return FileStatus.CLEAN
        return status.status_for(relative_path)

    def drop(self, repo_root: str) -> None:
        with QMutexLocker(self._mutex):
            self._by_root.pop(repo_root, None)
            self._generation.pop(repo_root, None)

    def known_roots(self) -> list[str]:
        with QMutexLocker(self._mutex):
            return list(self._by_root.keys())
