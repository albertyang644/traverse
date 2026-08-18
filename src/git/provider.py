"""Git backend: repository discovery + `git status` parsing.

No Qt imports here on purpose -- this module is plain Python + subprocess so
the parsing logic can be unit-tested without a QApplication and without
mocking threads.
"""

from __future__ import annotations

import os
import subprocess

from src.vcs.provider import VcsProvider
from src.vcs.status import FileStatus, RepoStatus, worst

# git status --porcelain=v2 XY codes -> FileStatus.
# X = index (staged) state, Y = worktree state. See git-status(1) "Porcelain
# Format Version 2". We treat "staged with no worktree changes" as STAGED,
# and let worktree changes (M/D) take priority since that's what the user
# still needs to act on.
_WORKTREE_MAP = {
    "M": FileStatus.MODIFIED,
    "D": FileStatus.DELETED,
    "A": FileStatus.ADDED,
    "R": FileStatus.RENAMED,
    "C": FileStatus.RENAMED,   # copied -- close enough for an overlay icon
}
_INDEX_ONLY_MAP = {
    "M": FileStatus.STAGED,
    "A": FileStatus.STAGED,
    "D": FileStatus.STAGED,
    "R": FileStatus.STAGED,
    "C": FileStatus.STAGED,
}

_STATUS_TIMEOUT_SECS = 10


def _classify(index_ch: str, worktree_ch: str) -> FileStatus:
    if worktree_ch in _WORKTREE_MAP:
        return _WORKTREE_MAP[worktree_ch]
    if index_ch in _INDEX_ONLY_MAP:
        return _INDEX_ONLY_MAP[index_ch]
    return FileStatus.CLEAN


def parse_porcelain_v2(output: str) -> tuple[dict[str, FileStatus], str | None]:
    """Parse `git status --porcelain=v2 --branch -z` output.

    Returns (relative_path -> FileStatus, branch_name).
    Uses NUL-separated records ('-z') because file names may contain spaces,
    newlines, or be shown in the locale's native encoding -- porcelain v1's
    space-delimited, potentially-quoted format is not safely machine
    parseable in the general case.
    """
    files: dict[str, FileStatus] = {}
    branch: str | None = None

    records = output.split("\0")
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue

        if rec.startswith("# branch.head "):
            branch = rec[len("# branch.head "):].strip()
            continue
        if rec.startswith("#"):
            continue  # other header lines (branch.oid, branch.ab, ...)

        kind = rec[0]
        if kind == "1":
            # 1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>
            fields = rec.split(" ", 8)
            xy = fields[1]
            path = fields[8]
            files[path] = _classify(xy[0], xy[1])
        elif kind == "2":
            # 2 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <score> <path> -- an
            # extra <score> field (e.g. "R100") shifts path to index 9, and
            # the record is followed by a *separate* NUL field holding the
            # original path, which we skip over. Kind "2" always means
            # rename/copy, regardless of XY -- that takes priority over
            # "staged"/"modified" so the user sees it as a rename.
            fields = rec.split(" ", 9)
            path = fields[9]
            files[path] = FileStatus.RENAMED
            i += 1  # skip the "original path" NUL field
        elif kind == "u":
            # unmerged / conflicted: u <XY> <sub> ... <path>
            fields = rec.split(" ", 10)
            path = fields[-1]
            files[path] = FileStatus.CONFLICTED
        elif kind == "?":
            path = rec[2:]
            files[path] = FileStatus.UNTRACKED
        elif kind == "!":
            path = rec[2:]
            files[path] = FileStatus.IGNORED

    return files, branch


def build_dir_aggregate(files: dict[str, FileStatus]) -> dict[str, FileStatus]:
    """Roll per-file statuses up to every ancestor directory, so the tree
    view can badge a folder without walking its entire subtree at render
    time. O(files * average depth), computed once per refresh.
    """
    dirs: dict[str, FileStatus] = {}
    for path, status in files.items():
        if status in (FileStatus.CLEAN, FileStatus.IGNORED):
            continue
        parent = os.path.dirname(path)
        while parent:
            dirs[parent] = worst(dirs.get(parent, FileStatus.CLEAN), status)
            new_parent = os.path.dirname(parent)
            if new_parent == parent:
                break
            parent = new_parent
    return dirs


class GitProvider(VcsProvider):
    """VcsProvider implementation backed by the `git` CLI."""

    @property
    def name(self) -> str:
        return "git"

    def discover_root(self, start_dir: str) -> str | None:
        """Walk upward looking for a `.git` entry (directory for a normal
        repo, or file for a worktree/submodule). Pure filesystem checks --
        no subprocess -- so this is cheap enough to call on every navigation.
        """
        current = os.path.abspath(start_dir)
        while True:
            if os.path.exists(os.path.join(current, ".git")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return None
            current = parent

    def read_status(self, repo_root: str, generation: int) -> RepoStatus:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain=v2", "--branch", "-z", "--ignored=matching"],
                cwd=repo_root,
                capture_output=True,
                timeout=_STATUS_TIMEOUT_SECS,
            )
        except FileNotFoundError:
            return RepoStatus(repo_root, None, generation=generation,
                               error="git executable not found on PATH")
        except subprocess.TimeoutExpired:
            return RepoStatus(repo_root, None, generation=generation,
                               error=f"git status timed out after {_STATUS_TIMEOUT_SECS}s")

        if proc.returncode != 0:
            return RepoStatus(repo_root, None, generation=generation,
                               error=proc.stderr.decode(errors="replace").strip() or "git status failed")

        output = proc.stdout.decode(errors="replace")
        files, branch = parse_porcelain_v2(output)
        dir_status = build_dir_aggregate(files)
        return RepoStatus(repo_root, branch, files=files, dir_status=dir_status, generation=generation)

    def watch_paths(self, repo_root: str) -> list[str]:
        git_dir = os.path.join(repo_root, ".git")
        paths = [repo_root]
        for name in ("HEAD", "index", "refs", "MERGE_HEAD"):
            p = os.path.join(git_dir, name)
            if os.path.exists(p):
                paths.append(p)
        return paths
