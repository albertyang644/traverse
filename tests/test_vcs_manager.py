"""Integration tests for VcsManager against real git repos in tmp_path.

Needs a QApplication because VcsManager uses QTimer/QThreadPool/QFileSystemWatcher.
"""

import subprocess
import time

import pytest
from PyQt6.QtWidgets import QApplication

from src.vcs.manager import VcsManager
from src.vcs.status import FileStatus


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _init_repo(path):
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return path


def _pump_until(qapp, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_ensure_tracking_discovers_and_refreshes(qapp, tmp_path):
    repo = _init_repo(tmp_path / "repo1")
    mgr = VcsManager()
    owner = object()

    root = mgr.ensure_tracking(str(repo), watcher_owner=owner)
    assert root == str(repo)

    assert _pump_until(qapp, lambda: mgr.repo_status(root) is not None)
    assert mgr.branch_for(root) in ("main", "master")


def test_status_for_reflects_modified_file(qapp, tmp_path):
    repo = _init_repo(tmp_path / "repo2")
    mgr = VcsManager()
    owner = object()
    mgr.ensure_tracking(str(repo), watcher_owner=owner)
    assert _pump_until(qapp, lambda: mgr.repo_status(str(repo)) is not None)

    (repo / "a.py").write_text("x = 2\n")
    mgr.invalidate(str(repo))
    assert _pump_until(
        qapp,
        lambda: mgr.status_for(str(repo / "a.py")) == FileStatus.MODIFIED,
    )


def test_non_repo_directory_returns_clean(qapp, tmp_path):
    plain = tmp_path / "not_a_repo"
    plain.mkdir()
    mgr = VcsManager()
    owner = object()
    root = mgr.ensure_tracking(str(plain), watcher_owner=owner)
    assert root is None
    assert mgr.status_for(str(plain / "whatever.txt")) == FileStatus.CLEAN


def test_two_repos_tracked_independently(qapp, tmp_path):
    repo_a = _init_repo(tmp_path / "repoA")
    repo_b = _init_repo(tmp_path / "repoB")
    mgr = VcsManager()
    owner_a, owner_b = object(), object()

    mgr.ensure_tracking(str(repo_a), watcher_owner=owner_a)
    mgr.ensure_tracking(str(repo_b), watcher_owner=owner_b)
    assert _pump_until(
        qapp,
        lambda: mgr.repo_status(str(repo_a)) is not None and mgr.repo_status(str(repo_b)) is not None,
    )

    (repo_a / "a.py").write_text("changed\n")
    mgr.invalidate(str(repo_a))
    assert _pump_until(qapp, lambda: mgr.status_for(str(repo_a / "a.py")) == FileStatus.MODIFIED)

    # repo_b must be unaffected by repo_a's refresh
    assert mgr.status_for(str(repo_b / "a.py")) == FileStatus.CLEAN


def test_is_repo_root_and_dir_aggregate(qapp, tmp_path):
    repo = _init_repo(tmp_path / "repo3")
    (repo / "sub").mkdir()
    (repo / "sub" / "b.py").write_text("y = 1\n")
    mgr = VcsManager()
    owner = object()
    mgr.ensure_tracking(str(repo), watcher_owner=owner)
    assert _pump_until(qapp, lambda: mgr.repo_status(str(repo)) is not None)

    assert mgr.is_repo_root(str(repo)) is True
    assert mgr.is_repo_root(str(repo / "sub")) is False
    assert mgr.status_for_dir(str(repo / "sub")) == FileStatus.UNTRACKED
