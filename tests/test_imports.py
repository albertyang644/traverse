"""Smoke tests: verify all src modules import without errors."""

import pytest


def test_import_main_window():
    from src.main_window import MainWindow


def test_import_file_pane():
    from src.file_pane import FilePane, FilePaneWidget


def test_import_file_list():
    from src.file_list import FileListPane


def test_import_tree_panel():
    from src.tree_panel import TreePanel


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.actions"),
    reason="src/actions.py not yet written"
)
def test_import_actions():
    from src.actions import Actions


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.search"),
    reason="src/search.py not yet written"
)
def test_import_search():
    from src.search import search_files


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.bookmarks"),
    reason="src/bookmarks.py not yet written"
)
def test_import_bookmarks():
    from src.bookmarks import BookmarkManager


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.state_manager"),
    reason="src/state_manager.py not yet written"
)
def test_import_state_manager():
    from src.state_manager import StateManager


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.settings"),
    reason="src/settings.py not yet written"
)
def test_import_settings():
    from src.settings import SettingsDialog
