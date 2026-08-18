"""Off-UI-thread execution of VcsProvider.read_status.

Uses QThreadPool + QRunnable rather than one QThread per repo: opening many
tabs/repos must not spawn unbounded threads (or unbounded git processes --
QThreadPool's default max thread count already caps concurrency to a small
multiple of the CPU count), and QThreadPool reuses worker threads instead of
paying OS thread-creation cost on every refresh.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from src.vcs.provider import VcsProvider
from src.vcs.status import RepoStatus


class _WorkerSignals(QObject):
    finished = pyqtSignal(object)   # RepoStatus


class StatusRefreshTask(QRunnable):
    """Computes RepoStatus for one repo on a QThreadPool thread, then emits
    the result back onto whichever thread owns _WorkerSignals (the UI
    thread, since VcsManager -- a QObject created on the UI thread -- owns
    the signals instance that connects to it).
    """

    def __init__(self, provider: VcsProvider, repo_root: str, generation: int):
        super().__init__()
        self._provider = provider
        self._repo_root = repo_root
        self._generation = generation
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            status = self._provider.read_status(self._repo_root, self._generation)
        except Exception as exc:  # noqa: BLE001 -- a worker thread must never raise
            status = RepoStatus(self._repo_root, None, generation=self._generation, error=str(exc))
        try:
            self.signals.finished.emit(status)
        except RuntimeError:
            # The application can close while a background VCS read is still
            # finishing. Qt may already have destroyed the signal receiver.
            pass
