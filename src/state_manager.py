"""Unified state manager — bookmarks, pane dirs, hotkeys, columns."""

import json
import os
from pathlib import Path


# Set TRAVERSE_STATE_DIR to run against a throwaway state directory instead
# of the one in the repo. The test suite relies on this: without it,
# constructing a MainWindow writes the real session.json, so running the
# tests overwrote the developer's saved panes, tabs and window geometry.
ENV_STATE_DIR = "TRAVERSE_STATE_DIR"


def default_state_dir() -> Path:
    """The state directory to use when a caller does not name one."""
    return Path(os.environ.get(ENV_STATE_DIR)
                or Path(__file__).resolve().parents[1] / "state")


class StateManager:
    """Reads/writes state JSON files in state_dir."""

    def __init__(self, state_dir=None):
        self._dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self._bookmarks: list[str] = []
        self._pane_dirs: dict[str, str] = {
            "left": os.path.expanduser("~"),
            "right": os.path.expanduser("~"),
        }
        self._hotkeys: dict[str, str] = {}
        self._bash_actions: list[dict] = []
        self._columns: dict = {}
        self._defaults: dict[str, str] = {}   # mime → exec_cmd
        self._font: dict = {}                  # {family, size}
        self._window: dict = {}               # geometry, splitters, flags
        self._quick_buttons: list[dict] = [] # {path, abbrev, color}

    def load(self):
        bk = self._read("bookmarks.json", {})
        raw = bk.get("bookmarks", [])
        self._bookmarks = raw if isinstance(raw, list) else []

        session = self._read("session.json", {})
        for k, v in session.items():
            if k.endswith("_pane_dir") and isinstance(v, str):
                pane = k[: -len("_pane_dir")]
                self._pane_dirs[pane] = v
        font_data = session.get("font")
        if isinstance(font_data, dict):
            self._font = font_data
        win_data = session.get("window")
        if isinstance(win_data, dict):
            self._window = win_data
        qb = session.get("quick_buttons")
        if isinstance(qb, list):
            self._quick_buttons = qb

        hk = self._read("hotkeys.json", {})
        self._hotkeys = hk.get("actions", {}) if isinstance(hk, dict) else {}
        if self._hotkeys.get("new_file") == "Ctrl+T" and "new_tab" not in self._hotkeys:
            self._hotkeys["new_file"] = "Ctrl+N"
        self._bash_actions = hk.get("bash_actions", []) if isinstance(hk, dict) else []

        self._columns = self._read("columns.json", {})
        self._defaults = self._read("defaults.json", {})

    def save(self):
        self._write("bookmarks.json", {"bookmarks": self._bookmarks})
        session = {f"{k}_pane_dir": v for k, v in self._pane_dirs.items()}
        session["font"] = self._font
        session["window"] = self._window
        session["quick_buttons"] = self._quick_buttons
        self._write("session.json", session)
        self._write("hotkeys.json", {"actions": self._hotkeys, "bash_actions": self._bash_actions})
        self._write("columns.json", self._columns)
        self._write("defaults.json", self._defaults)

    # ── Bookmarks ──────────────────────────────────────────────────

    def add_bookmark(self, path: str):
        if path not in self._bookmarks:
            self._bookmarks.append(path)

    def remove_bookmark(self, path: str):
        if path in self._bookmarks:
            self._bookmarks.remove(path)

    def get_bookmarks(self) -> list[str]:
        return list(self._bookmarks)

    # ── Pane dirs ─────────────────────────────────────────────────

    def set_pane_dir(self, pane_id: str, directory: str):
        self._pane_dirs[pane_id] = directory

    def get_pane_dir(self, pane_id: str) -> str:
        return self._pane_dirs.get(pane_id, os.path.expanduser("~"))

    # ── Hotkeys ───────────────────────────────────────────────────

    def set_hotkey(self, action: str, key_seq: str):
        self._hotkeys[action] = key_seq

    def get_hotkey(self, action: str) -> str | None:
        return self._hotkeys.get(action)

    def get_hotkeys(self) -> dict[str, str]:
        return dict(self._hotkeys)

    # ── Bash actions ──────────────────────────────────────────────

    def get_bash_actions(self) -> list[dict]:
        return list(self._bash_actions)

    def set_bash_actions(self, actions: list[dict]):
        self._bash_actions = list(actions)

    # ── Columns ───────────────────────────────────────────────────

    def set_columns(self, pane_id: str, columns: list):
        self._columns[pane_id] = columns

    def get_columns(self, pane_id: str) -> list:
        return self._columns.get(pane_id, [])

    # ── Quick buttons ─────────────────────────────────────────────

    def get_quick_buttons(self) -> list[dict]:
        return list(self._quick_buttons)

    def set_quick_buttons(self, buttons: list[dict]):
        self._quick_buttons = list(buttons)

    # ── Window state ──────────────────────────────────────────────

    def set_window_state(self, data: dict):
        self._window = data

    def get_window_state(self) -> dict:
        return dict(self._window)

    # ── Font ──────────────────────────────────────────────────────

    def set_font(self, family: str, size: int):
        self._font = {"family": family, "size": size}

    def get_font(self) -> dict:
        return dict(self._font)

    # ── Default apps ──────────────────────────────────────────────

    def set_default(self, mime: str, cmd: str):
        self._defaults[mime] = cmd

    def get_default(self, mime: str) -> str | None:
        return self._defaults.get(mime)

    # ── I/O ───────────────────────────────────────────────────────

    def _read(self, filename, default):
        p = self._dir / filename
        try:
            return json.loads(p.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def _write(self, filename, data):
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / filename).write_text(json.dumps(data, indent=2))
