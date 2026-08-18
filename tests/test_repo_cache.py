"""Tests for the thread-safe RepoStatusCache. Uses real QMutex, but no
QApplication is required since QMutex works without an event loop."""

from src.vcs.cache import RepoStatusCache
from src.vcs.status import FileStatus, RepoStatus


def test_store_and_lookup():
    cache = RepoStatusCache()
    gen = cache.next_generation("/repo")
    status = RepoStatus("/repo", "main", files={"a.py": FileStatus.MODIFIED}, generation=gen)
    assert cache.store(status) is True
    assert cache.status_for_path("/repo", "a.py") == FileStatus.MODIFIED
    assert cache.status_for_path("/repo", "untouched.py") == FileStatus.CLEAN


def test_unknown_repo_returns_clean():
    cache = RepoStatusCache()
    assert cache.status_for_path("/nowhere", "x.py") == FileStatus.CLEAN


def test_stale_generation_is_dropped():
    """A slow refresh that finishes after a newer one must not clobber it --
    this is what lets VcsManager fire off a fresh refresh without waiting
    for an in-flight one to finish."""
    cache = RepoStatusCache()
    gen1 = cache.next_generation("/repo")
    gen2 = cache.next_generation("/repo")

    fresh = RepoStatus("/repo", "main", files={"a.py": FileStatus.MODIFIED}, generation=gen2)
    stale = RepoStatus("/repo", "main", files={"a.py": FileStatus.CLEAN}, generation=gen1)

    assert cache.store(fresh) is True
    assert cache.store(stale) is False   # arrives late, must be rejected
    assert cache.status_for_path("/repo", "a.py") == FileStatus.MODIFIED


def test_drop_clears_entry():
    cache = RepoStatusCache()
    gen = cache.next_generation("/repo")
    cache.store(RepoStatus("/repo", "main", generation=gen))
    cache.drop("/repo")
    assert cache.get("/repo") is None
