"""Abstract VCS backend interface.

Adding a new VCS (SVN, Mercurial, ...) means implementing this interface and
registering an instance with VcsManager -- no changes to the manager, cache,
or UI code are needed. This is the one seam where inheritance beats
composition: callers need to invoke "discover" / "read_status" without
caring which VCS answered, and Python has no structural-typing-friendly way
to express that other than an ABC or a Protocol. Everything downstream of
this interface (cache, manager, UI) is plain composition.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.vcs.status import RepoStatus


class VcsProvider(ABC):
    """One version-control backend. Must be safe to call from a worker thread --
    implementations must not touch Qt widgets.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'git'. Used in logs and as a cache-key prefix."""

    @abstractmethod
    def discover_root(self, start_dir: str) -> str | None:
        """Walk upward from start_dir and return the repository root, or None
        if start_dir is not inside a repository this provider understands.
        Must be cheap (filesystem checks only, no subprocess) so it can run
        on every navigation without a noticeable delay.
        """

    @abstractmethod
    def read_status(self, repo_root: str, generation: int) -> RepoStatus:
        """Synchronously compute a full RepoStatus for repo_root. Runs on a
        worker thread -- may spawn a subprocess. Must never raise for
        ordinary VCS errors; put a message in RepoStatus.error instead.
        """

    @abstractmethod
    def watch_paths(self, repo_root: str) -> list[str]:
        """Paths whose changes should trigger a refresh of this repo (e.g.
        .git/HEAD, .git/index, .git/refs) in addition to the working tree
        itself.
        """
