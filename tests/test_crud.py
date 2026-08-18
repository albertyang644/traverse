"""Tests for CRUD file operations (create, rename, delete)."""

import os
import pytest
from pathlib import Path


pytestmark = pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.actions"),
    reason="src/actions.py not yet written"
)


@pytest.fixture
def work_dir(tmp_path):
    return tmp_path


def test_create_directory(work_dir):
    from src.actions import create_directory
    new_dir = work_dir / "new_folder"
    create_directory(str(new_dir))
    assert new_dir.is_dir()


def test_create_directory_already_exists(work_dir):
    from src.actions import create_directory
    existing = work_dir / "exists"
    existing.mkdir()
    # Should not raise
    create_directory(str(existing))


def test_create_file(work_dir):
    from src.actions import create_file
    new_file = work_dir / "notes.txt"
    create_file(str(new_file))
    assert new_file.exists()


def test_create_file_does_not_overwrite(work_dir):
    from src.actions import create_file
    f = work_dir / "data.txt"
    f.write_text("original")
    create_file(str(f))
    assert f.read_text() == "original"


def test_rename_file(work_dir):
    from src.actions import rename_path
    src = work_dir / "old.txt"
    src.write_text("hello")
    dst = work_dir / "new.txt"
    rename_path(str(src), str(dst))
    assert dst.exists()
    assert not src.exists()


def test_rename_directory(work_dir):
    from src.actions import rename_path
    src = work_dir / "old_dir"
    src.mkdir()
    dst = work_dir / "new_dir"
    rename_path(str(src), str(dst))
    assert dst.is_dir()
    assert not src.exists()


def test_delete_file(work_dir):
    from src.actions import delete_path
    f = work_dir / "to_delete.txt"
    f.write_text("bye")
    delete_path(str(f))
    assert not f.exists()


def test_delete_directory(work_dir):
    from src.actions import delete_path
    d = work_dir / "to_delete_dir"
    d.mkdir()
    (d / "child.txt").write_text("child")
    delete_path(str(d))
    assert not d.exists()


def test_delete_nonexistent_raises(work_dir):
    from src.actions import delete_path
    with pytest.raises((FileNotFoundError, OSError)):
        delete_path(str(work_dir / "ghost.txt"))
