"""Pure-Python tests for porcelain v2 parsing and repo discovery.

No QApplication needed here -- src/git/provider.py has zero Qt dependency,
which is exactly why the split between vcs/provider.py and vcs/worker.py
exists.
"""

import os
import subprocess

import pytest

from src.git.provider import GitProvider, build_dir_aggregate, parse_porcelain_v2
from src.vcs.status import FileStatus


def _record(*fields: str) -> str:
    return " ".join(fields)


def test_parse_modified_and_staged():
    # "1" record: X=index state, Y=worktree state
    out = _record("1", "M.", "N...", "100644", "100644", "100644", "aaa", "bbb", "staged_only.py") + "\0" \
        + _record("1", ".M", "N...", "100644", "100644", "100644", "aaa", "bbb", "worktree_only.py") + "\0"
    files, branch = parse_porcelain_v2(out)
    assert files["staged_only.py"] == FileStatus.STAGED
    assert files["worktree_only.py"] == FileStatus.MODIFIED
    assert branch is None


def test_parse_untracked_and_ignored():
    out = "? new_file.txt\0! build/output.o\0"
    files, _ = parse_porcelain_v2(out)
    assert files["new_file.txt"] == FileStatus.UNTRACKED
    assert files["build/output.o"] == FileStatus.IGNORED


def test_parse_conflicted():
    out = _record("u", "AA", "N...", "100644", "100644", "100644", "100644",
                   "aaa", "bbb", "ccc", "conflicted.py") + "\0"
    files, _ = parse_porcelain_v2(out)
    assert files["conflicted.py"] == FileStatus.CONFLICTED


def test_parse_renamed_skips_original_path_field():
    out = _record("2", "R.", "N...", "100644", "100644", "100644",
                   "aaa", "bbb", "R100", "new_name.py") + "\0" + "old_name.py" + "\0" \
        + "? after_rename.txt" + "\0"
    files, _ = parse_porcelain_v2(out)
    assert files["new_name.py"] == FileStatus.RENAMED
    assert "old_name.py" not in files          # skipped, not misparsed as its own entry
    assert files["after_rename.txt"] == FileStatus.UNTRACKED


def test_parse_branch_header():
    out = "# branch.oid abcd1234\0# branch.head main\0" \
        + _record("1", ".M", "N...", "100644", "100644", "100644", "aaa", "bbb", "x.py") + "\0"
    files, branch = parse_porcelain_v2(out)
    assert branch == "main"
    assert files["x.py"] == FileStatus.MODIFIED


def test_dir_aggregate_rolls_up_and_skips_clean():
    files = {
        "src/pkg/deep/file.py": FileStatus.MODIFIED,
        "top_level_clean.py": FileStatus.CLEAN,
        "top_level_ignored.log": FileStatus.IGNORED,
    }
    dirs = build_dir_aggregate(files)
    assert dirs["src/pkg/deep"] == FileStatus.MODIFIED
    assert dirs["src/pkg"] == FileStatus.MODIFIED
    assert dirs["src"] == FileStatus.MODIFIED
    assert "" not in dirs   # top-level files don't produce an empty-string entry


def test_dir_aggregate_picks_worst_status():
    files = {
        "src/a.py": FileStatus.MODIFIED,
        "src/b.py": FileStatus.CONFLICTED,
    }
    dirs = build_dir_aggregate(files)
    assert dirs["src"] == FileStatus.CONFLICTED


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("hello\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    return repo


def test_discover_root_finds_git_dir_from_nested_path(git_repo):
    nested = git_repo / "a" / "b" / "c"
    nested.mkdir(parents=True)
    provider = GitProvider()
    assert provider.discover_root(str(nested)) == str(git_repo)


def test_discover_root_returns_none_outside_repo(tmp_path):
    provider = GitProvider()
    assert provider.discover_root(str(tmp_path)) is None


def test_read_status_reports_modified_and_untracked(git_repo):
    (git_repo / "tracked.txt").write_text("changed\n")
    (git_repo / "new.txt").write_text("new\n")
    provider = GitProvider()
    status = provider.read_status(str(git_repo), generation=1)
    assert status.error is None
    assert status.branch in ("main", "master")
    assert status.files["tracked.txt"] == FileStatus.MODIFIED
    assert status.files["new.txt"] == FileStatus.UNTRACKED
    assert status.is_dirty


def test_read_status_on_missing_git_binary(monkeypatch, git_repo):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    provider = GitProvider()
    status = provider.read_status(str(git_repo), generation=1)
    assert status.error is not None
    assert status.files == {}


def test_watch_paths_include_head_and_index(git_repo):
    provider = GitProvider()
    paths = provider.watch_paths(str(git_repo))
    assert str(git_repo) in paths
    assert os.path.join(str(git_repo), ".git", "HEAD") in paths
