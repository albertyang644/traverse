"""VCS-agnostic status data structures.

These types are shared by every version-control backend (Git today; SVN/Hg
could reuse them tomorrow). Nothing here imports Qt or subprocess — it is
plain data so it can be unit-tested without a QApplication and without git
installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FileStatus(Enum):
    """One file's VCS state. Ordered roughly by how "urgent" it is to show,
    used by directory aggregation to pick the single worst status to badge
    a folder with.
    """
    CLEAN = "clean"
    IGNORED = "ignored"
    UNTRACKED = "untracked"
    STAGED = "staged"
    ADDED = "added"
    MODIFIED = "modified"
    RENAMED = "renamed"
    DELETED = "deleted"
    CONFLICTED = "conflicted"

    @property
    def priority(self) -> int:
        """Higher wins when aggregating many files into one folder badge."""
        return _PRIORITY[self]


_PRIORITY = {
    FileStatus.CLEAN: 0,
    FileStatus.IGNORED: 1,
    FileStatus.UNTRACKED: 2,
    FileStatus.STAGED: 3,
    FileStatus.ADDED: 3,
    FileStatus.RENAMED: 4,
    FileStatus.MODIFIED: 5,
    FileStatus.DELETED: 6,
    FileStatus.CONFLICTED: 7,
}


def worst(a: FileStatus, b: FileStatus) -> FileStatus:
    return a if a.priority >= b.priority else b


@dataclass(frozen=True)
class RepoStatus:
    """Immutable snapshot of one repository's status.

    Replacing the whole object on refresh (rather than mutating it in place)
    means readers on the UI thread never observe a half-updated cache --
    they either hold the old snapshot or the new one.
    """
    repo_root: str
    branch: str | None
    files: dict[str, FileStatus] = field(default_factory=dict)   # relative path -> status
    dir_status: dict[str, FileStatus] = field(default_factory=dict)  # relative dir path -> aggregated status
    generation: int = 0     # monotonically increasing; lets callers detect staleness cheaply
    error: str | None = None

    def status_for(self, relative_path: str) -> FileStatus:
        if relative_path in self.files:
            return self.files[relative_path]
        if relative_path in self.dir_status:
            return self.dir_status[relative_path]
        return FileStatus.CLEAN

    @property
    def is_dirty(self) -> bool:
        return any(s not in (FileStatus.CLEAN, FileStatus.IGNORED) for s in self.files.values())
