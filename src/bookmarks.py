"""Bookmark management: data class + optional UI panel."""

import json
import os
from pathlib import Path

from src.state_manager import default_state_dir


class BookmarkManager:
    """Manages a list of bookmarked directory paths, persisted to JSON."""

    def __init__(self, filepath: str | Path | None = None):
        self._path = Path(filepath) if filepath else default_state_dir() / "bookmarks.json"
        self._bookmarks: list[str] = []
        self.load()

    def load(self):
        try:
            data = json.loads(self._path.read_text())
            raw = data.get("bookmarks", [])
            self._bookmarks = raw if isinstance(raw, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            self._bookmarks = []

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"bookmarks": self._bookmarks}, indent=2))

    def add(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        if path not in self._bookmarks:
            self._bookmarks.append(path)
            self.save()

    def remove(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        if path in self._bookmarks:
            self._bookmarks.remove(path)
            self.save()

    def get_all(self) -> list[str]:
        return list(self._bookmarks)


# ── Optional Qt UI panel ──────────────────────────────────────────────────────

def _make_bookmarks_panel(parent=None):
    """Return a QWidget panel for managing bookmarks. Import Qt lazily."""
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
        QPushButton, QMessageBox,
    )
    from PyQt6.QtCore import Qt, pyqtSignal

    class BookmarksPanel(QWidget):
        navigate_requested = pyqtSignal(str)

        def __init__(self, manager: BookmarkManager, parent=None):
            super().__init__(parent)
            self._mgr = manager
            v = QVBoxLayout(self)

            self._list = QListWidget()
            self._list.itemDoubleClicked.connect(self._on_double_click)
            v.addWidget(self._list)

            btns = QHBoxLayout()
            add_btn = QPushButton("Add current dir")
            add_btn.clicked.connect(self._add)
            rm_btn = QPushButton("Remove")
            rm_btn.clicked.connect(self._remove)
            btns.addWidget(add_btn)
            btns.addWidget(rm_btn)
            v.addLayout(btns)

            self._refresh()

        def _refresh(self):
            self._list.clear()
            for p in self._mgr.get_all():
                self._list.addItem(p)

        def _on_double_click(self, item: QListWidgetItem):
            self.navigate_requested.emit(item.text())

        def _add(self):
            # Signal parent to get current dir
            if hasattr(self.parent(), "current_dir"):
                self._mgr.add(self.parent().current_dir)
                self._refresh()

        def _remove(self):
            item = self._list.currentItem()
            if item:
                self._mgr.remove(item.text())
                self._refresh()

    return BookmarksPanel(BookmarkManager(), parent)
