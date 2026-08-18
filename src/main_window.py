"""Main application window."""

import os
import shlex
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QLabel, QMessageBox, QInputDialog, QMenu,
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit,
    QDialogButtonBox, QPushButton, QToolButton, QColorDialog, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QFileDialog,
)
from PyQt6.QtGui import QAction, QKeySequence, QColor
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QPoint, QEvent

from src.file_pane import FilePaneWidget
from src.state_manager import StateManager
from src.vcs.manager import VcsManager

# The state directory is resolved by StateManager alone, so an override
# (TRAVERSE_STATE_DIR) applies everywhere rather than being bypassed here.


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n} PB"


class _QuickBookmarkDialog(QDialog):
    """Pick a bookmark, assign a 3-letter abbreviation and a color."""

    def __init__(self, bookmarks: list[str], parent=None, initial: dict | None = None, path_locked: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Edit Bookmark Button" if initial else "Quick Add Bookmark Button")
        self.setMinimumWidth(400)
        self._color = QColor(initial.get("color", "#4a9eff") if initial else "#4a9eff")
        self._data: dict | None = None

        v = QVBoxLayout(self)

        # Bookmark picker
        v.addWidget(QLabel("Bookmark:"))
        self._combo = QComboBox()
        for bk in bookmarks:
            self._combo.addItem(bk)
        if initial and initial.get("path"):
            idx = self._combo.findText(initial["path"])
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
        self._combo.setEnabled(not path_locked)
        v.addWidget(self._combo)

        # Abbreviation
        v.addWidget(QLabel("3-letter abbreviation:"))
        self._abbrev = QLineEdit()
        self._abbrev.setMaxLength(3)
        self._abbrev.setPlaceholderText("e.g.  HOM")
        if initial and initial.get("abbrev"):
            self._abbrev.setText(initial["abbrev"])
        v.addWidget(self._abbrev)

        # Color picker
        h = QHBoxLayout()
        h.addWidget(QLabel("Button color:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(56, 26)
        self._color_btn.setToolTip("Click to pick a color")
        self._color_btn.clicked.connect(self._pick_color)
        h.addWidget(self._color_btn)
        h.addStretch()
        v.addLayout(h)

        # Live preview
        v.addWidget(QLabel("Preview:"))
        self._preview = QToolButton()
        self._preview.setFixedSize(48, 28)
        self._preview.setEnabled(False)
        v.addWidget(self._preview)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self._on_ok)
        bbox.rejected.connect(self.reject)
        v.addWidget(bbox)

        self._abbrev.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _pick_color(self):
        c = QColorDialog.getColor(self._color, self, "Pick Button Color")
        if c.isValid():
            self._color = c
            self._refresh_preview()

    def _refresh_preview(self):
        text = self._abbrev.text().upper() or "???"
        fg = "#000000" if self._color.lightness() > 128 else "#ffffff"
        css = (f"background-color:{self._color.name()}; color:{fg}; "
               f"font-weight:bold; border:1px solid #555; border-radius:3px;")
        self._color_btn.setStyleSheet(f"background-color:{self._color.name()}; border:1px solid #888;")
        self._preview.setText(text)
        self._preview.setStyleSheet(f"QToolButton{{{css}}}")

    def _on_ok(self):
        abbrev = self._abbrev.text().strip().upper()
        if not abbrev:
            QMessageBox.warning(self, "Missing abbreviation",
                                "Please enter up to 3 letters for the button label.")
            return
        self._data = {
            "path":   self._combo.currentText(),
            "abbrev": abbrev,
            "color":  self._color.name(),
        }
        self.accept()

    def result_data(self) -> dict | None:
        return self._data


class _QuickBookmarkButton(QToolButton):
    """Quick bookmark button with delayed drag-to-reorder."""

    reorder_started = pyqtSignal(object)
    reorder_moved = pyqtSignal(object, QPoint)
    reorder_finished = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = QPoint()
        self._reordering = False
        self._used_reorder = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(2000)
        self._hold_timer.timeout.connect(self._begin_reorder)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._reordering = False
            self._used_reorder = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._reordering:
            self.reorder_moved.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        if self._hold_timer.isActive():
            delta = event.position().toPoint() - self._press_pos
            if delta.manhattanLength() > 20:
                self._hold_timer.stop()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._hold_timer.stop()
        if self._reordering:
            self._reordering = False
            self._used_reorder = True
            self.setDown(False)
            self.unsetCursor()
            self.releaseMouse()
            self.reorder_finished.emit(self)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._hold_timer.stop()
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)

    def nextCheckState(self):
        if self._used_reorder:
            self._used_reorder = False
            return
        super().nextCheckState()

    def _begin_reorder(self):
        if not (self.isDown() and self.isEnabled()):
            return
        self._reordering = True
        self.grabMouse()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.reorder_started.emit(self)


class _SearchWorker(QThread):
    found = pyqtSignal(str)             # one matching path per emit
    # Deliberately not named `finished`: that would shadow QThread's own
    # thread-exit signal, so the usual `worker.finished.connect(cleanup)`
    # idiom would instead fire from inside run() while the thread is still
    # alive — releasing the worker there aborts Qt.
    search_finished = pyqtSignal(int)   # total count when done

    def __init__(self, root: str, pattern: str, recursive: bool):
        super().__init__()
        self._root = root
        self._pattern = pattern
        self._recursive = recursive
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        import fnmatch
        count = 0
        for dirpath, dirnames, filenames in os.walk(self._root):
            if self._stop:
                break
            base = os.path.basename(dirpath)
            if dirpath != self._root and fnmatch.fnmatch(base.lower(), self._pattern.lower()):
                self.found.emit(dirpath)
                count += 1
            for name in filenames:
                if self._stop:
                    break
                if fnmatch.fnmatch(name.lower(), self._pattern.lower()):
                    self.found.emit(os.path.join(dirpath, name))
                    count += 1
            if not self._recursive:
                dirnames[:] = []
        self.search_finished.emit(count)


class _SearchResultsWindow(QDialog):
    """Non-modal results window — streams matches live, supports copy/move/delete."""

    _COL_NAME = 0
    _COL_DIR  = 1
    _COL_SIZE = 2
    _COL_TYPE = 3

    def __init__(self, root: str, pattern: str, recursive: bool, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._worker = None
        self.setWindowTitle(f"Find results — {pattern}")
        self.setMinimumSize(820, 480)
        self.resize(980, 560)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        v = QVBoxLayout(self)

        # Info bar
        self._status = QLabel(f'Searching  {root}  for  "{pattern}" ...')
        v.addWidget(self._status)

        # Results table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Name", "Directory", "Size", "Type"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setStretchLastSection(False)
        for col in range(4):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(self._COL_NAME, 260)
        self._table.setColumnWidth(self._COL_DIR, 520)
        self._table.setColumnWidth(self._COL_SIZE, 90)
        self._table.setColumnWidth(self._COL_TYPE, 110)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)  # re-enabled once search finishes; see _on_finished
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        v.addWidget(self._table, 1)

        # Action buttons
        btns = QHBoxLayout()
        def _btn(label, tip, slot):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            btns.addWidget(b)
            return b
        _btn("Copy to Other Pane",  "Copy selected to the other pane's directory", self._copy_to_other)
        _btn("Copy to…",            "Copy selected to a chosen directory",          self._copy_to_dir)
        btns.addSpacing(12)
        _btn("Move to Other Pane",  "Move selected to the other pane's directory", self._move_to_other)
        _btn("Move to…",            "Move selected to a chosen directory",          self._move_to_dir)
        btns.addSpacing(12)
        _btn("Delete",              "Delete selected files/folders",                self._delete_selected)
        btns.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btns.addWidget(close_btn)
        v.addLayout(btns)

        # Start search
        self._worker = _SearchWorker(root, pattern, recursive)
        self._worker.found.connect(self._on_found)
        self._worker.search_finished.connect(self._on_finished)
        self._worker.start()

    # ── Search callbacks ──────────────────────────────────────────

    def _on_found(self, path: str):
        row = self._table.rowCount()
        self._table.insertRow(row)
        is_dir = os.path.isdir(path)
        try:
            size = "" if is_dir else _fmt_bytes(os.path.getsize(path))
        except OSError:
            size = ""
        ftype = "Directory" if is_dir else (os.path.splitext(path)[1].lstrip(".").upper() or "File")
        items = [
            QTableWidgetItem(os.path.basename(path)),
            QTableWidgetItem(os.path.dirname(path)),
            QTableWidgetItem(size),
            QTableWidgetItem(ftype),
        ]
        for col, item in enumerate(items):
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._table.setItem(row, col, item)
        count = self._table.rowCount()
        self._status.setText(f"Searching…  {count} found so far")

    def _on_finished(self, total: int):
        self._status.setText(
            f"{total} result{'s' if total != 1 else ''} found" if total
            else "No matches found."
        )
        self._table.setSortingEnabled(True)

    # ── Selection helpers ─────────────────────────────────────────

    def _selected_paths(self) -> list[str]:
        rows = {i.row() for i in self._table.selectedIndexes()}
        paths = []
        for r in sorted(rows):
            item = self._table.item(r, 0)
            if item:
                paths.append(item.data(Qt.ItemDataRole.UserRole))
        return paths

    def _remove_rows(self, paths: set[str]):
        """Remove rows whose path is in *paths* (after move/delete)."""
        for row in range(self._table.rowCount() - 1, -1, -1):
            item = self._table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) in paths:
                self._table.removeRow(row)
        self._on_finished(self._table.rowCount())

    # ── Double-click → open file, or navigate into directory ───────

    def _on_double_click(self, index):
        item = self._table.item(index.row(), 0)
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            self._open_path(path)

    def _open_path(self, path: str):
        pane = self._mw._active_pane()
        if os.path.isdir(path):
            pane.navigate(path)
        else:
            pane._open_file(path)

    def _navigate_to_parent(self):
        paths = self._selected_paths()
        if paths:
            self._mw._active_pane().navigate(os.path.dirname(paths[0]))

    # ── Context menu ──────────────────────────────────────────────

    def _context_menu(self, pos):
        if not self._selected_paths():
            return
        menu = QMenu(self)
        menu.addAction(QAction("Open",                menu, triggered=lambda: self._open_path(self._selected_paths()[0])))
        menu.addSeparator()
        menu.addAction(QAction("Copy to Other Pane",  menu, triggered=self._copy_to_other))
        menu.addAction(QAction("Copy to…",            menu, triggered=self._copy_to_dir))
        menu.addSeparator()
        menu.addAction(QAction("Move to Other Pane",  menu, triggered=self._move_to_other))
        menu.addAction(QAction("Move to…",            menu, triggered=self._move_to_dir))
        menu.addSeparator()
        menu.addAction(QAction("Delete",              menu, triggered=self._delete_selected))
        menu.addSeparator()
        menu.addAction(QAction("Navigate to parent",  menu, triggered=self._navigate_to_parent))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ── File operations ───────────────────────────────────────────

    def _copy_to_other(self):
        self._do_copy(self._mw._other_pane().current_dir)

    def _copy_to_dir(self):
        dst = QFileDialog.getExistingDirectory(self, "Copy to…", self._mw._active_pane().current_dir)
        if dst:
            self._do_copy(dst)

    def _move_to_other(self):
        self._do_move(self._mw._other_pane().current_dir)

    def _move_to_dir(self):
        dst = QFileDialog.getExistingDirectory(self, "Move to…", self._mw._active_pane().current_dir)
        if dst:
            self._do_move(dst)

    def _do_copy(self, dst: str):
        # Delegate to the pane so the transfer runs on a worker thread with a
        # progress dialog, and tolerates targets without Unix metadata (MTP).
        paths = self._selected_paths()
        if paths:
            self._mw._active_pane().copy_paths_to(paths, dst)

    def _do_move(self, dst: str):
        paths = self._selected_paths()
        if not paths:
            return
        pane = self._mw._active_pane()

        def prune_moved_rows():
            pane.filesystem_changed.disconnect(prune_moved_rows)
            self._remove_rows({p for p in paths if not os.path.exists(p)})

        pane.filesystem_changed.connect(prune_moved_rows)
        pane.move_paths_to(paths, dst)

    def _delete_selected(self):
        import shutil
        paths = self._selected_paths()
        if not paths:
            return
        names = "\n".join(os.path.basename(p) for p in paths[:10])
        if len(paths) > 10:
            names += f"\n…and {len(paths) - 10} more"
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Permanently delete {len(paths)} item(s)?\n\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted, errors = set(), []
        for path in paths:
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                deleted.add(path)
            except OSError as e:
                errors.append(str(e))
        self._mw._left.refresh()
        self._mw._right.refresh()
        self._remove_rows(deleted)
        if errors:
            QMessageBox.warning(self, "Delete errors", "\n".join(errors))

    def closeEvent(self, event):
        # This window is WA_DeleteOnClose, so once it goes the worker loses
        # its last reference. A thread still running at that point aborts Qt,
        # and wait() can time out on a slow filesystem, so hand any laggard
        # to the main window to hold until it exits on its own.
        worker = self._worker
        if worker and worker.isRunning():
            worker.stop()
            if not worker.wait(500):
                self._mw.adopt_orphan_worker(worker)
        self._worker = None
        super().closeEvent(event)


class _SearchDialog(QDialog):
    """Find files from the active pane's current directory."""

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find")
        self.setMinimumWidth(460)
        self._data: dict | None = None

        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"Find in: {root}"))

        self._pattern = QLineEdit()
        self._pattern.setPlaceholderText("Pattern, e.g. *.txt")
        v.addWidget(self._pattern)

        self._recursive = QCheckBox("Traverse tree")
        self._recursive.setChecked(False)
        v.addWidget(self._recursive)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self._on_ok)
        bbox.rejected.connect(self.reject)
        v.addWidget(bbox)

        self._pattern.setFocus()

    def _on_ok(self):
        pattern = self._pattern.text().strip()
        if not pattern:
            return
        self._data = {
            "pattern": pattern,
            "recursive": self._recursive.isChecked(),
        }
        self.accept()

    def result_data(self) -> dict | None:
        return self._data


class MainWindow(QMainWindow):
    """Traverse — dual-pane file manager."""

    def __init__(self):
        super().__init__()
        self._state = StateManager()
        self._state.load()
        self._dual = True
        self._orphan_workers: set = set()

        self.setWindowTitle("Traverse")
        self.setMinimumSize(1024, 768)

        self._named_actions: dict[str, QAction] = {}
        self._bash_actions: list[QAction] = []
        self._quick_btns: dict[QToolButton, dict] = {}   # widget → data
        self._quick_btn_actions: dict[QToolButton, QAction] = {}
        self._quick_btn_acts: list[QAction] = []          # for removeAction()
        self._quick_reorder_btn: QToolButton | None = None
        self._quick_press_btn: QToolButton | None = None
        self._quick_reordering = False
        self._quick_hold_timer = QTimer(self)
        self._quick_hold_timer.setSingleShot(True)
        self._quick_hold_timer.setInterval(2000)
        self._quick_hold_timer.timeout.connect(self._begin_quick_reorder)

        self._vcs = VcsManager(parent=self)
        self._vcs.repo_status_changed.connect(self._on_repo_status_changed_for_titlebar)
        self._build_panes()
        self._build_menus()
        self._build_global_actions()
        self._build_toolbar()
        self._build_status_bar()
        self._apply_font()
        self._apply_hotkeys()
        self._apply_bash_actions()

        from PyQt6.QtWidgets import QApplication
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self._left.set_active(True)

        self._restore_window_state()
        QTimer.singleShot(0, self._update_status)

    # ── Panes ─────────────────────────────────────────────────────

    def _build_panes(self):
        left_dir  = self._state.get_pane_dir("left")
        right_dir = self._state.get_pane_dir("right")

        self._left  = FilePaneWidget(start_dir=left_dir,  state_manager=self._state, vcs_manager=self._vcs)
        self._right = FilePaneWidget(start_dir=right_dir, state_manager=self._state, vcs_manager=self._vcs)

        self._left.dir_changed.connect(self._on_dir_changed)
        self._right.dir_changed.connect(self._on_dir_changed)
        self._left.file_list.selection_changed.connect(self._update_status)
        self._right.file_list.selection_changed.connect(self._update_status)

        # Tree right-click signals
        for pane in (self._left, self._right):
            pane.tree.bookmark_requested.connect(self._add_bookmark_path)
            pane.tree.open_terminal_requested.connect(self._open_terminal)
            pane.open_terminal_requested.connect(self._open_terminal)
            pane.filesystem_changed.connect(self._refresh_panes_after_filesystem_change)
            pane.clipboard_changed.connect(self._on_clipboard_changed)

        # Restore and persist column visibility/order per pane
        left_cols = self._state.get_columns("left")
        if left_cols:
            self._left.file_list.set_column_config(left_cols)
        right_cols = self._state.get_columns("right")
        if right_cols:
            self._right.file_list.set_column_config(right_cols)

        self._left.file_list.column_config_changed.connect(
            lambda config: (self._state.set_columns("left", config), self._state.save())
        )
        self._right.file_list.column_config_changed.connect(
            lambda config: (self._state.set_columns("right", config), self._state.save())
        )

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._left)
        self._splitter.addWidget(self._right)
        self._splitter.setSizes([500, 500])

        self.setCentralWidget(self._splitter)
        self._on_clipboard_changed()

    def _active_pane(self) -> FilePaneWidget:
        """Return whichever pane contains the current keyboard focus."""
        w = self.focusWidget()
        while w is not None:
            if w is self._left:
                return self._left
            if w is self._right:
                return self._right
            w = w.parent()
        return self._left

    def _other_pane(self) -> FilePaneWidget:
        return self._right if self._active_pane() is self._left else self._left

    # ── Menu bar ─────────────────────────────────────────────────

    def _build_menus(self):
        mb = self.menuBar()

        # File
        fm = mb.addMenu("&File")
        self._act(fm, "New &Folder",     "Ctrl+Shift+N", lambda: self._active_pane().new_folder(), "new_folder")
        self._act(fm, "New F&ile",       "Ctrl+N",       lambda: self._active_pane().new_file(),   "new_file")
        fm.addSeparator()
        self._act(fm, "New &Tab",        "Ctrl+T",       lambda: self._active_pane().new_tab(),    "new_tab")
        self._act(fm, "&Close Tab",      "Ctrl+W",       lambda: self._active_pane().close_tab(),  "close_tab")
        self._act(fm, "&Next Tab",       "Ctrl+Tab",       lambda: self._active_pane().cycle_tab(1),  "next_tab")
        self._act(fm, "&Previous Tab",   "Ctrl+Shift+Tab", lambda: self._active_pane().cycle_tab(-1), "prev_tab")
        fm.addSeparator()
        self._act(fm, "&Refresh",        "Ctrl+R",       lambda: self._active_pane().refresh(),    "refresh")
        fm.addSeparator()
        self._act(fm, "E&xit",           "Alt+F4",       self.close)

        # Edit
        em = mb.addMenu("&Edit")
        self._act(em, "&Rename",         "F2",     lambda: self._active_pane().rename_selected(), "rename")
        self._act(em, "&Delete",         "Delete", lambda: self._active_pane().delete_selected(), "delete")
        em.addSeparator()
        self._act(em, "Copy to &other pane", "F5", self._copy_to_other, "copy_to_other")
        self._act(em, "Mo&ve to other pane", "F6", self._move_to_other, "move_to_other")
        em.addSeparator()
        self._act(em, "Get &Size",   "Ctrl+Shift+S",
                  lambda: self._active_pane().get_size_selected(), "get_size")

        # View
        self._view_menu = mb.addMenu("&View")
        vm = self._view_menu
        self._act(vm, "Toggle &Dual Pane",  "F3",      self._toggle_dual,   "toggle_dual")
        self._act(vm, "Toggle &Trees",       "Ctrl+\\", self._toggle_trees)
        vm.addSeparator()
        self._act(vm, "&Find…",              "Ctrl+F",  self._open_search,  "search")

        # Bookmarks
        self._bk_menu = mb.addMenu("&Bookmarks")
        self._act(self._bk_menu, "&Add current dir", "Ctrl+B", self._add_bookmark, "add_bookmark")
        self._bk_menu.addSeparator()
        self._rebuild_bookmarks_menu()

        # Tools
        tm = mb.addMenu("&Tools")
        self._act(tm, "&Settings…", "Ctrl+,", self._open_settings)

    def _act(self, menu, label, shortcut, slot, action_key=None):
        a = QAction(label, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        if action_key:
            self._named_actions[action_key] = a
        return a

    def _build_global_actions(self):
        for action_key, label, slot in [
            ("open_selected", "Open Selected", lambda: self._active_pane().open_selected()),
            ("open_with", "Open With", lambda: self._active_pane().open_with_selected()),
            ("cut", "Cut", lambda: self._active_pane().cut_selected()),
            ("copy", "Copy", lambda: self._active_pane().copy_selected()),
            ("paste", "Paste", lambda: self._active_pane().paste()),
            ("duplicate", "Duplicate", lambda: self._active_pane().duplicate_selected()),
            ("select_all", "Select All", lambda: self._active_pane().select_all()),
            ("open_terminal", "Open Terminal Here", self._open_terminal_active),
            ("extract_here", "Extract Here", lambda: self._active_pane().extract_selected_here()),
            ("extract_to_folder", "Extract to Folder", lambda: self._active_pane().extract_selected_to_folder()),
            ("extract_archive_here", "Extract Archive Here", lambda: self._active_pane().extract_archive_here()),
            ("create_archive", "Create Archive", lambda: self._active_pane().create_archive_selected()),
            ("copy_path", "Copy Path", lambda: self._active_pane().copy_paths_selected()),
            ("copy_checksum", "Copy SHA-256", lambda: self._active_pane().copy_checksum_selected()),
            ("permissions", "Permissions", lambda: self._active_pane().edit_permissions_selected()),
            ("owner_group", "Owner / Group", lambda: self._active_pane().edit_owner_group_selected()),
            ("properties", "Properties", lambda: self._active_pane().show_properties_selected()),
        ]:
            a = QAction(label, self)
            a.triggered.connect(slot)
            self.addAction(a)
            self._named_actions[action_key] = a

    # ── Toolbar ──────────────────────────────────────────────────

    def _build_toolbar(self):
        tb = self.addToolBar("Navigation")
        tb.setObjectName("NavigationToolbar")
        tb.setMovable(False)
        tb.setToolTip("Navigation toolbar")

        for text, tip, slot, action_key in [
            ("←", "Back  (Alt+Left)",              lambda: self._active_pane().go_back(),                          "go_back"),
            ("→", "Forward  (Alt+Right)",           lambda: self._active_pane().go_forward(),                       "go_forward"),
            ("↑", "Parent Directory  (Alt+Up)",     lambda: self._active_pane().go_up(),                            "go_up"),
            ("⌂", "Home Directory  (Alt+Home)",     lambda: self._active_pane().navigate(os.path.expanduser("~")), "go_home"),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)
            self._named_actions[action_key] = a

        tb.addSeparator()

        for text, tip, slot in [
            ("📁+", "New Folder  (Ctrl+Shift+N)", lambda: self._active_pane().new_folder()),
            ("📄+", "New File  (Ctrl+N)",         lambda: self._active_pane().new_file()),
            ("＋", "New Tab  (Ctrl+T)",           lambda: self._active_pane().new_tab()),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)

        tb.addSeparator()

        for text, tip, slot in [
            ("✏", "Rename  (F2)",      lambda: self._active_pane().rename_selected()),
            ("🗑", "Delete  (Delete)",  lambda: self._active_pane().delete_selected()),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)

        tb.addSeparator()

        for text, tip, slot in [
            ("💾", "Jump to Devices",              self._jump_to_devices),
            ("🌐", "Jump to Network Neighborhood",  self._jump_to_network),
        ]:
            a = QAction(text, self)
            a.setToolTip(tip)
            a.triggered.connect(slot)
            tb.addAction(a)

        # Separator before quick bookmark buttons
        tb.addSeparator()
        self._nav_toolbar = tb

        # Custom right-click menu on toolbar
        tb.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tb.customContextMenuRequested.connect(self._toolbar_context_menu)

        # Let the View menu show/hide this toolbar (toggleViewAction is pre-wired)
        toggle = tb.toggleViewAction()
        toggle.setText("Show &Navigation Toolbar")
        self._view_menu.insertAction(self._view_menu.actions()[0], toggle)
        self._view_menu.insertSeparator(self._view_menu.actions()[1])
        self._rebuild_quick_buttons()

    def _jump_to_devices(self):
        pane = self._active_pane()
        pane.set_tree_visible(True)
        pane.tree.reveal_devices()

    def _jump_to_network(self):
        pane = self._active_pane()
        pane.set_tree_visible(True)
        pane.tree.reveal_network()

    # ── Status bar ────────────────────────────────────────────────

    def _build_status_bar(self):
        self._status_lbl = QLabel("Ready")
        self.statusBar().addWidget(self._status_lbl, 1)

    def _update_status(self):
        if not hasattr(self, "_left"):
            return
        pane = self._active_pane()
        path = pane.current_dir
        count = pane.file_list.item_count()
        selected = len(pane.file_list.selected_paths())
        try:
            sv = os.statvfs(path)
            free = _fmt_bytes(sv.f_frsize * sv.f_bavail)
        except (OSError, AttributeError):
            free = "?"
        sel_str = f"  {selected} selected" if selected else ""
        total_str = ""
        if selected:
            sel_bytes, partial = pane.file_list.selection_size()
            if partial and sel_bytes == 0:
                # Only unmeasured folders selected; "0 B+" would read as empty.
                total_str = "  |  size unknown (Get Size)"
            else:
                # "+" marks a total that omits directories nobody has measured,
                # so a folder in the selection cannot silently understate it.
                total_str = f"  |  {_fmt_bytes(sel_bytes)}{'+' if partial else ''} total"
        branch_str = ""
        repo_root = pane.current_repo_root()
        if repo_root is not None:
            branch = self._vcs.branch_for(repo_root)
            branch_str = f"  |  git:{branch}" if branch else "  |  git:(loading…)"
        self._status_lbl.setText(
            f"{count} items{sel_str}{total_str}  |  {free} free{branch_str}"
        )
        self.setWindowTitle(f"Traverse — {path}")

    # ── Dual-pane toggle ─────────────────────────────────────────

    def adopt_orphan_worker(self, worker):
        """Keep a reference to a worker whose owner window is closing.

        Dropping a QThread that is still running aborts the process, so the
        window that outlives it holds on until the thread actually exits.
        """
        worker.finished.connect(lambda w=worker: self._orphan_workers.discard(w))
        self._orphan_workers.add(worker)

    def _toggle_dual(self):
        self._dual = not self._dual
        if self._dual:
            self._left.setVisible(True)
            self._right.setVisible(True)
            self._ensure_dual_layout()
        else:
            self._other_pane().setVisible(False)

    def _ensure_dual_layout(self):
        # A pane that was hidden can come back with zero widths; give
        # both panes an even split and sane inner tree/list sizes.
        sizes = self._splitter.sizes()
        if len(sizes) != 2 or min(sizes) < 100:
            total = max(sum(sizes), self.width(), 800)
            self._splitter.setSizes([total // 2, total // 2])
        for pane in (self._left, self._right):
            pane.ensure_sane_layout()

    def _toggle_trees(self):
        self._trees_visible = not getattr(self, "_trees_visible", True)
        for pane in (self._left, self._right):
            pane.set_tree_visible(self._trees_visible)

    # ── Cross-pane ops ────────────────────────────────────────────

    def _copy_to_other(self):
        active = self._active_pane()
        other  = self._other_pane()
        active.copy_to(other.current_dir)
        other.refresh()

    def _move_to_other(self):
        active = self._active_pane()
        other  = self._other_pane()
        active.move_to(other.current_dir)
        other.refresh()

    def _refresh_panes_after_filesystem_change(self):
        self._left.refresh()
        self._right.refresh()

    def _on_clipboard_changed(self):
        cut_paths = FilePaneWidget.current_cut_paths()
        self._left.file_list.set_cut_paths(cut_paths)
        self._right.file_list.set_cut_paths(cut_paths)

    # ── Search ───────────────────────────────────────────────────

    def _open_search(self):
        root = self._active_pane().current_dir
        dlg = _SearchDialog(root, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if not data:
            return
        win = _SearchResultsWindow(
            root, data["pattern"], data["recursive"],
            main_window=self, parent=self,
        )
        win.show()

    # ── Bookmarks ─────────────────────────────────────────────────

    def _add_bookmark(self):
        self._add_bookmark_path(self._active_pane().bookmark_location())

    def _add_bookmark_path(self, path: str):
        persistent = self._active_pane().tree.smb_uri_for_path(path) or path
        self._state.add_bookmark(persistent)
        self._state.save()
        self._rebuild_bookmarks_menu()

    def _open_terminal(self, path: str):
        for cmd in (["konsole", "--workdir", path],
                    ["gnome-terminal", f"--working-directory={path}"],
                    ["xfce4-terminal", f"--working-directory={path}"],
                    ["xterm", "-e", f"cd {shlex.quote(path)} && $SHELL"]):
            try:
                subprocess.Popen(cmd)
                return
            except FileNotFoundError:
                continue
        QMessageBox.warning(self, "Terminal", "No supported terminal emulator found.")

    def _open_terminal_active(self):
        self._open_terminal(self._active_pane().current_dir)

    def _rebuild_bookmarks_menu(self):
        actions = self._bk_menu.actions()
        # Keep first two (Add + separator)
        for a in actions[2:]:
            self._bk_menu.removeAction(a)
        abbrev_map = {d["path"]: d["abbrev"] for d in self._state.get_quick_buttons()}
        for bk in self._state.get_bookmarks():
            label = f"{bk}  [{abbrev_map[bk]}]" if bk in abbrev_map else bk
            a = QAction(label, self)
            a.triggered.connect(lambda checked=False, p=bk: self._active_pane().open_location(p))
            self._bk_menu.addAction(a)

    # ── Quick bookmark buttons ────────────────────────────────────

    def _toolbar_context_menu(self, pos):
        # Detect if the click landed on one of our quick buttons
        child = self._nav_toolbar.childAt(pos)
        w = child
        while w is not None and w is not self._nav_toolbar:
            if w in self._quick_btns:
                data = self._quick_btns[w]
                menu = QMenu(self)
                menu.addAction(QAction(
                    f'Rename / Recolor "{data["abbrev"]}"…', menu,
                    triggered=lambda checked=False, d=data: self._edit_quick_button(d)
                ))
                menu.addSeparator()
                menu.addAction(QAction(
                    f'Remove "{data["abbrev"]}"  ({data["path"]})', menu,
                    triggered=lambda checked=False, d=data: self._remove_quick_button(d)
                ))
                menu.exec(self._nav_toolbar.mapToGlobal(pos))
                return
            w = w.parent()

        # Generic toolbar area
        menu = QMenu(self)
        bks = self._state.get_bookmarks()
        if bks:
            menu.addAction(QAction("Quick Add Bookmark Button…", menu,
                                   triggered=self._show_add_quick_dialog))
        else:
            a = QAction("No bookmarks yet — add one with Ctrl+B", menu)
            a.setEnabled(False)
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(self._nav_toolbar.toggleViewAction())
        menu.exec(self._nav_toolbar.mapToGlobal(pos))

    def _show_add_quick_dialog(self):
        bks = self._state.get_bookmarks()
        if not bks:
            QMessageBox.information(self, "No Bookmarks",
                                    "Add bookmarks first with Ctrl+B.")
            return
        dlg = _QuickBookmarkDialog(bks, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if not data:
            return
        # Replace if path already has a button, otherwise append
        buttons = [b for b in self._state.get_quick_buttons()
                   if b["path"] != data["path"]]
        buttons.append(data)
        self._state.set_quick_buttons(buttons)
        self._state.save()
        self._rebuild_quick_buttons()
        self._rebuild_bookmarks_menu()

    def _edit_quick_button(self, data: dict):
        bks = self._state.get_bookmarks()
        if data.get("path") and data["path"] not in bks:
            bks = [data["path"], *bks]
        dlg = _QuickBookmarkDialog(bks, parent=self, initial=data, path_locked=True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        edited = dlg.result_data()
        if not edited:
            return
        buttons = []
        replaced = False
        for button in self._state.get_quick_buttons():
            if not replaced and button.get("path") == data.get("path"):
                buttons.append(edited)
                replaced = True
            else:
                buttons.append(button)
        if not replaced:
            buttons.append(edited)
        self._state.set_quick_buttons(buttons)
        self._state.save()
        self._rebuild_quick_buttons()
        self._rebuild_bookmarks_menu()

    def _rebuild_quick_buttons(self):
        self._quick_hold_timer.stop()
        self._quick_press_btn = None
        self._quick_reordering = False
        for act in self._quick_btn_acts:
            self._nav_toolbar.removeAction(act)
        self._quick_btn_acts.clear()
        self._quick_btns.clear()
        self._quick_btn_actions.clear()
        self._quick_reorder_btn = None
        for data in self._state.get_quick_buttons():
            self._add_quick_button_to_toolbar(data)

    def _add_quick_button_to_toolbar(self, data: dict):
        path   = data["path"]
        abbrev = data["abbrev"]
        color  = data["color"]
        qc = QColor(color)
        fg = "#000000" if qc.lightness() > 128 else "#ffffff"
        css = (f"QToolButton{{background-color:{color};color:{fg};"
               f"font-weight:bold;border:1px solid #555;border-radius:3px;"
               f"padding:2px 5px;min-width:32px;}}"
               f"QToolButton:hover{{border:2px solid #fff;}}")
        btn = QToolButton()
        btn.setText(abbrev)
        btn.setStyleSheet(css)
        btn.setToolTip(f"{path}\nHold for 2 seconds, then drag left/right to reorder.")
        btn.clicked.connect(lambda checked=False, p=path: self._active_pane().open_location(p))
        btn.installEventFilter(self)
        act = self._nav_toolbar.addWidget(btn)
        self._quick_btns[btn] = data
        self._quick_btn_actions[btn] = act
        self._quick_btn_acts.append(act)

    def eventFilter(self, obj, event):
        if obj in getattr(self, "_quick_btns", {}):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._quick_press_btn = obj
                self._quick_reordering = False
                self._quick_hold_timer.start()
                return False

            if event.type() == QEvent.Type.MouseMove:
                if self._quick_reordering and obj is self._quick_press_btn:
                    self._quick_reorder_moved(obj, event.globalPosition().toPoint())
                    return True
                return False

            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self._quick_hold_timer.stop()
                if self._quick_reordering and obj is self._quick_press_btn:
                    self._quick_reordering = False
                    obj.setDown(False)
                    obj.unsetCursor()
                    obj.releaseMouse()
                    self._quick_reorder_finished(obj)
                    self._quick_press_btn = None
                    return True
                self._quick_press_btn = None
                return False

            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._quick_hold_timer.stop()
                self._quick_press_btn = None
                self._quick_reordering = False

        return super().eventFilter(obj, event)

    def _begin_quick_reorder(self):
        btn = self._quick_press_btn
        if btn is None or btn not in self._quick_btns or not btn.isVisible():
            return
        self._quick_reordering = True
        btn.setDown(True)
        btn.setCursor(Qt.CursorShape.ClosedHandCursor)
        btn.grabMouse()
        self._quick_reorder_started(btn)

    def _quick_reorder_started(self, btn):
        self._quick_reorder_btn = btn
        self.statusBar().showMessage("Drag bookmark button left or right to reorder", 3000)

    def _quick_reorder_moved(self, btn, global_pos: QPoint):
        if btn is not self._quick_reorder_btn or btn not in self._quick_btn_actions:
            return
        widgets = [self._nav_toolbar.widgetForAction(a) for a in self._quick_btn_acts]
        if btn not in widgets:
            return

        toolbar_x = self._nav_toolbar.mapFromGlobal(global_pos).x()
        target_idx = 0
        for widget in widgets:
            center_x = widget.x() + widget.width() // 2
            if toolbar_x > center_x:
                target_idx += 1
        target_idx = max(0, min(target_idx, len(self._quick_btn_acts) - 1))

        current_idx = widgets.index(btn)
        if target_idx == current_idx:
            return

        action = self._quick_btn_actions[btn]
        self._quick_btn_acts.pop(current_idx)
        self._quick_btn_acts.insert(target_idx, action)

        before = self._quick_btn_acts[target_idx + 1] if target_idx + 1 < len(self._quick_btn_acts) else None
        if before is not None:
            self._nav_toolbar.insertAction(before, action)
        else:
            self._nav_toolbar.addAction(action)

    def _quick_reorder_finished(self, btn):
        if btn is not self._quick_reorder_btn:
            return
        self._quick_reorder_btn = None
        ordered = []
        for action in self._quick_btn_acts:
            widget = self._nav_toolbar.widgetForAction(action)
            data = self._quick_btns.get(widget)
            if data:
                ordered.append(data)
        self._state.set_quick_buttons(ordered)
        self._state.save()
        self._rebuild_bookmarks_menu()
        self.statusBar().showMessage("Bookmark button order saved", 2000)

    def _remove_quick_button(self, data: dict):
        buttons = [b for b in self._state.get_quick_buttons()
                   if b["path"] != data["path"]]
        self._state.set_quick_buttons(buttons)
        self._state.save()
        self._rebuild_quick_buttons()
        self._rebuild_bookmarks_menu()

    # ── Settings ─────────────────────────────────────────────────

    def _open_settings(self):
        from src.settings import SettingsDialog
        dlg = SettingsDialog(self._state, parent=self)
        if dlg.exec():
            self._apply_font()
            self._apply_hotkeys()
            self._apply_bash_actions()

    def _apply_font(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QFont
        font_data = self._state.get_font()
        if not font_data.get("family"):
            return
        f = QFont(font_data["family"], font_data.get("size", 10))
        QApplication.instance().setFont(f)
        # QTableView ignores QApplication font changes when a stylesheet is set;
        # push the font explicitly to both file list panels.
        for pane in (self._left, self._right):
            pane.file_list.apply_font(f)

    def _apply_hotkeys(self):
        """Apply saved hotkeys to all named actions (falls back to defaults)."""
        from src.settings import _DEFAULT_HOTKEYS
        saved = self._state.get_hotkeys()
        for key, action in self._named_actions.items():
            shortcut = saved[key] if key in saved else _DEFAULT_HOTKEYS.get(key, "")
            action.setShortcut(QKeySequence(shortcut) if shortcut else QKeySequence())

    def _apply_bash_actions(self):
        """Rebuild hotkeys for bash actions, replacing any previously registered."""
        for a in self._bash_actions:
            self.removeAction(a)
        self._bash_actions.clear()

        for ba in self._state.get_bash_actions():
            name = ba.get("name", "")
            cmd  = ba.get("command", "")
            key  = ba.get("hotkey", "")
            if cmd and key:
                a = QAction(name or cmd, self)
                a.setShortcut(QKeySequence(key))
                a.triggered.connect(
                    lambda checked=False, c=cmd: subprocess.run(
                        c, shell=True, cwd=self._active_pane().current_dir
                    )
                )
                self.addAction(a)
                self._bash_actions.append(a)

    # ── Events ───────────────────────────────────────────────────

    def _on_focus_changed(self, _old, new):
        if new is None:
            return
        active = self._active_pane()
        self._left.set_active(active is self._left)
        self._right.set_active(active is self._right)
        QTimer.singleShot(0, self._update_status)

    def _on_dir_changed(self, _path: str):
        QTimer.singleShot(0, self._update_status)

    def _on_repo_status_changed_for_titlebar(self, _repo_root: str):
        self._update_status()

    def closeEvent(self, event):
        self._state.set_pane_dir("left",  self._left.current_dir)
        self._state.set_pane_dir("right", self._right.current_dir)
        self._state.set_window_state({
            "geometry":       self.saveGeometry().toBase64().data().decode(),
            "main_state":     self.saveState().toBase64().data().decode(),
            "main_splitter":  self._splitter.sizes(),
            "left_splitter":  self._left.get_splitter_sizes(),
            "right_splitter": self._right.get_splitter_sizes(),
            "left_tabs":      self._left.tabs_state(),
            "right_tabs":     self._right.tabs_state(),
            "dual_pane":      self._dual,
            "left_tree_visible":  self._left.tree_visible(),
            "right_tree_visible": self._right.tree_visible(),
        })
        self._state.save()
        super().closeEvent(event)

    def _restore_window_state(self):
        from PyQt6.QtCore import QByteArray
        w = self._state.get_window_state()
        if not w:
            return
        if w.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(w["geometry"].encode()))
        if w.get("main_state"):
            self.restoreState(QByteArray.fromBase64(w["main_state"].encode()))
        if w.get("main_splitter"):
            self._splitter.setSizes(w["main_splitter"])
        if w.get("left_splitter"):
            self._left.set_splitter_sizes(w["left_splitter"])
        if w.get("right_splitter"):
            self._right.set_splitter_sizes(w["right_splitter"])
        if w.get("left_tabs"):
            self._left.set_tabs_state(w["left_tabs"])
        if w.get("right_tabs"):
            self._right.set_tabs_state(w["right_tabs"])
        if "dual_pane" in w:
            self._dual = w["dual_pane"]
            if not self._dual:
                self._right.setVisible(False)
        if "left_tree_visible" in w or "right_tree_visible" in w:
            self._left.set_tree_visible(w.get("left_tree_visible", True))
            self._right.set_tree_visible(w.get("right_tree_visible", True))
        elif "trees_visible" in w:  # legacy single flag
            for pane in (self._left, self._right):
                pane.set_tree_visible(w["trees_visible"])
        self._trees_visible = (self._left.tree_visible()
                               or self._right.tree_visible())
        if self._dual:
            self._ensure_dual_layout()
