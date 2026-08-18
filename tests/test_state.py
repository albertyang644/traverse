"""Tests for state persistence (bookmarks, session, hotkeys, columns)."""

import json
import pytest
from pathlib import Path


# No state file is tracked in the repo: bookmarks, hotkeys, columns,
# open-with defaults and session are all per-user runtime state that the
# app writes on first run. These tests therefore exercise StateManager
# against a throwaway directory rather than asserting on repo files.


def test_state_manager_works_with_no_state_dir(tmp_path):
    """A fresh checkout has no state/ at all; the app must still start."""
    from src.state_manager import StateManager
    st = StateManager(state_dir=tmp_path / "missing")
    st.load()
    assert st.get_bookmarks() == []
    assert isinstance(st.get_hotkeys(), dict)
    assert st.get_columns("left") == []


def test_state_files_are_created_on_save(tmp_path):
    from src.state_manager import StateManager
    st = StateManager(state_dir=tmp_path / "state")
    st.load()
    st.save()
    for name in ("bookmarks.json", "session.json", "hotkeys.json",
                 "columns.json", "defaults.json"):
        data = json.loads((tmp_path / "state" / name).read_text())
        assert isinstance(data, dict)


def test_bookmarks_round_trip(tmp_path):
    from src.state_manager import StateManager
    st = StateManager(state_dir=tmp_path)
    st.load()
    st.add_bookmark(str(tmp_path))
    st.save()
    reloaded = StateManager(state_dir=tmp_path)
    reloaded.load()
    assert reloaded.get_bookmarks() == [str(tmp_path)]


def test_session_round_trips_through_state_manager(tmp_path):
    """The behaviour the old presence check was standing in for."""
    from src.state_manager import StateManager
    st = StateManager(state_dir=tmp_path)
    st.set_pane_dir("left", "/tmp")
    st.set_window_state({"dual_pane": True, "main_splitter": [500, 500]})
    st.save()

    reloaded = StateManager(state_dir=tmp_path)
    reloaded.load()
    assert (tmp_path / "session.json").exists()
    assert reloaded.get_window_state()["dual_pane"] is True
    assert reloaded.get_window_state()["main_splitter"] == [500, 500]


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("src.state_manager"),
    reason="src/state_manager.py not yet written"
)
class TestStateManager:

    def test_save_and_load_bookmark(self, tmp_path):
        from src.state_manager import StateManager
        sm = StateManager(state_dir=tmp_path)
        sm.add_bookmark("/home/user/projects")
        sm.save()
        sm2 = StateManager(state_dir=tmp_path)
        sm2.load()
        assert "/home/user/projects" in sm2.get_bookmarks()

    def test_remove_bookmark(self, tmp_path):
        from src.state_manager import StateManager
        sm = StateManager(state_dir=tmp_path)
        sm.add_bookmark("/home/user/projects")
        sm.remove_bookmark("/home/user/projects")
        sm.save()
        sm2 = StateManager(state_dir=tmp_path)
        sm2.load()
        assert "/home/user/projects" not in sm2.get_bookmarks()

    def test_save_and_restore_pane_dirs(self, tmp_path):
        from src.state_manager import StateManager
        sm = StateManager(state_dir=tmp_path)
        sm.set_pane_dir("left", "/home/user")
        sm.set_pane_dir("right", "/tmp")
        sm.save()
        sm2 = StateManager(state_dir=tmp_path)
        sm2.load()
        assert sm2.get_pane_dir("left") == "/home/user"
        assert sm2.get_pane_dir("right") == "/tmp"

    def test_save_and_restore_hotkeys(self, tmp_path):
        from src.state_manager import StateManager
        sm = StateManager(state_dir=tmp_path)
        sm.set_hotkey("new_folder", "Ctrl+Shift+N")
        sm.save()
        sm2 = StateManager(state_dir=tmp_path)
        sm2.load()
        assert sm2.get_hotkey("new_folder") == "Ctrl+Shift+N"
