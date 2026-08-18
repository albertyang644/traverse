"""VcsManager -- the single coordinator UI code talks to.

Responsibilities:
  * repository discovery (memoized, cheap filesystem checks only)
  * scheduling status refreshes on a background thread pool
  * caching results (see cache.py) so UI reads never touch disk or git
  * watching for filesystem changes and re-refreshing only the affected repo
  * throttling so rapid navigation/fs-events don't spawn a git process per event

UI widgets only ever call: ensure_tracking(), status_for(), branch_for(),
refresh(), and connect to repo_status_changed. They never talk to a
VcsProvider, QProcess, or QThreadPool directly -- that separation is what
keeps file_list.py/tree_panel.py free of git-specific logic.
"""

from __future__ import annotations

import os

from PyQt6.QtCore import QObject, QThreadPool, QTimer, QFileSystemWatcher, pyqtSignal

from src.vcs.cache import RepoStatusCache
from src.vcs.provider import VcsProvider
from src.vcs.status import FileStatus, RepoStatus
from src.vcs.worker import StatusRefreshTask

# Coalesce bursts of filesystem events (e.g. `git checkout` touching dozens
# of files, or a rapid series of edits) into a single refresh instead of one
# git process per event.
_DEBOUNCE_MS = 300


class VcsManager(QObject):
    """One instance, shared across all panes/tabs, owned by MainWindow."""

    repo_status_changed = pyqtSignal(str)   # repo_root

    def __init__(self, providers: list[VcsProvider] | None = None, parent=None):
        super().__init__(parent)
        from src.git.provider import GitProvider
        self._providers = providers if providers is not None else [GitProvider()]

        self._cache = RepoStatusCache()
        self._pool = QThreadPool.globalInstance()

        self._root_for_dir: dict[str, str | None] = {}       # memoized discovery
        self._provider_for_root: dict[str, VcsProvider] = {}
        self._pending_timers: dict[str, QTimer] = {}          # repo_root -> debounce timer

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_watched_path_changed)
        self._watcher.fileChanged.connect(self._on_watched_path_changed)
        self._watched_dir_for_pane: dict[int, str] = {}       # id(pane) -> currently-watched dir

    # ── Discovery ────────────────────────────────────────────────────

    def _find_repo_root(self, directory: str) -> tuple[str | None, VcsProvider | None]:
        directory = os.path.abspath(directory)
        if directory in self._root_for_dir:
            root = self._root_for_dir[directory]
            return root, (self._provider_for_root.get(root) if root else None)

        for provider in self._providers:
            root = provider.discover_root(directory)
            if root is not None:
                self._root_for_dir[directory] = root
                self._provider_for_root[root] = provider
                return root, provider

        self._root_for_dir[directory] = None
        return None, None

    def repo_root_for(self, directory: str) -> str | None:
        root, _ = self._find_repo_root(directory)
        return root

    # ── Public status queries (UI thread, must be cheap) ────────────

    def status_for(self, path: str) -> FileStatus:
        directory = os.path.dirname(path)
        root, _ = self._find_repo_root(directory)
        if root is None:
            return FileStatus.CLEAN
        relative = os.path.relpath(path, root)
        return self._cache.status_for_path(root, relative)

    def status_for_dir(self, directory: str) -> FileStatus:
        """Aggregated status for a directory (used by the tree view). Cheap:
        one dict lookup into the already-cached RepoStatus.dir_status map,
        no filesystem walk.
        """
        directory = os.path.abspath(directory)
        root, _ = self._find_repo_root(directory)
        if root is None:
            return FileStatus.CLEAN
        if os.path.normpath(directory) == os.path.normpath(root):
            status = self._cache.get(root)
            return FileStatus.MODIFIED if (status is not None and status.is_dirty) else FileStatus.CLEAN
        relative = os.path.relpath(directory, root)
        return self._cache.status_for_path(root, relative)

    def is_repo_root(self, directory: str) -> bool:
        root, _ = self._find_repo_root(directory)
        return root is not None and os.path.normpath(root) == os.path.normpath(os.path.abspath(directory))

    def branch_for(self, repo_root: str) -> str | None:
        status = self._cache.get(repo_root)
        return status.branch if status else None

    def repo_status(self, repo_root: str) -> RepoStatus | None:
        return self._cache.get(repo_root)

    # ── Tracking a directory a pane just navigated to ───────────────

    def ensure_tracking(self, directory: str, watcher_owner: object) -> str | None:
        """Call whenever a pane navigates. Discovers the repo (if any),
        kicks off a refresh if it's never been fetched, and moves this
        pane's filesystem watch to the new directory. Returns the repo root
        (or None if not in a repository).
        """
        root, provider = self._find_repo_root(directory)

        self._rewatch_pane_dir(watcher_owner, directory)
        if root is not None:
            for p in provider.watch_paths(root):
                if p not in self._watcher.files() and p not in self._watcher.directories():
                    if os.path.isdir(p):
                        self._watcher.addPath(p)
                    elif os.path.exists(p):
                        self._watcher.addPath(p)
            if self._cache.get(root) is None:
                self.refresh(root, force=True)
        return root

    def _rewatch_pane_dir(self, watcher_owner: object, directory: str) -> None:
        key = id(watcher_owner)
        old = self._watched_dir_for_pane.get(key)
        if old == directory:
            return
        if old is not None and old in self._watcher.directories():
            # Only remove if no other pane still needs it.
            if old not in self._watched_dir_for_pane.values():
                self._watcher.removePath(old)
        if os.path.isdir(directory):
            self._watcher.addPath(directory)
        self._watched_dir_for_pane[key] = directory

    # ── Refresh scheduling ───────────────────────────────────────────

    def refresh(self, repo_root: str, force: bool = False) -> None:
        provider = self._provider_for_root.get(repo_root)
        if provider is None:
            return

        if not force:
            timer = self._pending_timers.get(repo_root)
            if timer is not None:
                timer.start(_DEBOUNCE_MS)  # already pending: just extend the debounce window
                return
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._start_refresh(repo_root))
            self._pending_timers[repo_root] = timer
            timer.start(_DEBOUNCE_MS)
            return

        self._start_refresh(repo_root)

    def _start_refresh(self, repo_root: str) -> None:
        self._pending_timers.pop(repo_root, None)
        provider = self._provider_for_root.get(repo_root)
        if provider is None:
            return
        generation = self._cache.next_generation(repo_root)
        task = StatusRefreshTask(provider, repo_root, generation)
        task.signals.finished.connect(self._on_status_ready)
        self._pool.start(task)

    def _on_status_ready(self, status: RepoStatus) -> None:
        if self._cache.store(status):
            self.repo_status_changed.emit(status.repo_root)

    # ── Filesystem change events ─────────────────────────────────────

    def _on_watched_path_changed(self, path: str) -> None:
        root = self.repo_root_for(path)
        if root is None:
            # Path itself might *be* a repo root or .git file that was
            # already resolved via a child directory; try matching by prefix.
            for known_root in self._provider_for_root:
                if path == known_root or path.startswith(known_root + os.sep):
                    root = known_root
                    break
        if root is not None:
            self.refresh(root)

    def invalidate(self, repo_root: str) -> None:
        """Explicit refresh, bypassing debounce/throttle (e.g. user pressed
        Refresh in the UI).
        """
        self._cache.drop(repo_root)
        self._start_refresh(repo_root)
