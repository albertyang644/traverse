"""Tests for recursive file search (simple + wildcard)."""

import os
import pytest
from pathlib import Path


pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.search"),
    reason="src/search.py not yet written"
)


@pytest.fixture
def sample_tree(tmp_path):
    """Create a small directory tree for search tests."""
    (tmp_path / "foo.txt").write_text("foo")
    (tmp_path / "bar.py").write_text("bar")
    (tmp_path / "baz.txt").write_text("baz")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "deep.txt").write_text("deep")
    (sub / "README.md").write_text("readme")
    nested = sub / "nested"
    nested.mkdir()
    (nested / "config.json").write_text("{}")
    return tmp_path


def test_simple_match(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "foo.txt")
    assert any("foo.txt" in r for r in results)


def test_simple_no_match(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "nonexistent.xyz")
    assert results == []


def test_wildcard_star(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "*.txt")
    names = [Path(r).name for r in results]
    assert "foo.txt" in names
    assert "baz.txt" in names
    assert "deep.txt" in names


def test_wildcard_question_mark(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "ba?.txt")
    names = [Path(r).name for r in results]
    assert "baz.txt" in names
    assert "foo.txt" not in names


def test_recursive_into_subdirs(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "*.json")
    assert any("config.json" in r for r in results)


def test_non_recursive_stays_in_current_dir(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "*.txt", recursive=False)
    names = [Path(r).name for r in results]
    assert "foo.txt" in names
    assert "baz.txt" in names
    assert "deep.txt" not in names


def test_case_insensitive(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "readme*")
    assert any("README.md" in r for r in results)


def test_returns_full_paths(sample_tree):
    from src.search import search_files
    results = search_files(str(sample_tree), "*.py")
    assert all(os.path.isabs(r) for r in results)
