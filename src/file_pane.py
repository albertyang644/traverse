"""FilePaneWidget — address bar + filter bar + tree + file list, all wired."""

import os
import shutil
import time
import subprocess
import tarfile
import hashlib
import stat
import glob

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLineEdit, QPushButton, QLabel, QMenu, QCompleter,
    QMessageBox, QInputDialog, QFileDialog, QTabBar, QApplication,
    QProgressDialog, QDialog, QDialogButtonBox,
)
from PyQt6.QtCore import (
    Qt, QEvent, pyqtSignal, QThread, pyqtSlot, QFileSystemWatcher, QTimer,
)
from PyQt6.QtGui import QAction, QColor, QKeySequence, QKeyEvent, QFileSystemModel
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

from src.tree_panel import TreePanel
from src.file_list import FileListPane
from src.transfer import TransferWorker


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_duration(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


class _DuWorker(QThread):
    """Runs `du -sb` per path, emitting one result at a time so cells update live."""
    result = pyqtSignal(str, object)   # (path, size_in_bytes) — object avoids 32-bit int overflow

    def __init__(self, paths: list[str]):
        super().__init__()
        self._paths = paths

    def run(self):
        for path in self._paths:
            try:
                r = subprocess.run(
                    ["du", "-sb", "--", path],
                    capture_output=True, text=True, timeout=60,
                )
                if r.returncode == 0 and r.stdout.strip():
                    size_bytes = int(r.stdout.strip().split("\t")[0])
                    self.result.emit(path, size_bytes)
            except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
                pass


class _PathLineEdit(QLineEdit):
    """Address bar with shell-like Tab completion for local paths."""

    tab_complete_requested = pyqtSignal()
    navigate_requested = pyqtSignal()

    def event(self, event):
        # QWidget normally consumes Tab for focus traversal before it reaches
        # keyPressEvent, so intercept it at the generic event boundary.
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Tab):
            self.tab_complete_requested.emit()
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent):
        # QCompleter may consume Return before QLineEdit emits returnPressed.
        # Handle it here so entering a valid path always navigates.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.navigate_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _RenameDialog(QDialog):
    """Rename prompt sized to the actual filename.

    QInputDialog.getText gives a ~250px field regardless of content, so a
    normal media filename had to be scrolled a few characters at a time.
    Here the field spans the whole dialog on its own row, the dialog is
    sized to fit the name (bounded so it can never exceed the screen), and
    the buttons sit on the row below.
    """

    _MIN_WIDTH = 620
    _MARGIN = 120          # dialog chrome around the text

    def __init__(self, old_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename")
        self.setSizeGripEnabled(True)          # drag wider for absurd names

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("New name:"))

        self._edit = QLineEdit(old_name)
        self._edit.setMinimumHeight(28)
        self._edit.selectAll()
        layout.addWidget(self._edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        layout.addWidget(buttons)               # own row, right-aligned by Qt

        # Return in the field accepts, as it did with QInputDialog.
        self._edit.returnPressed.connect(self.accept)

        self.resize(self._width_for(old_name), self.sizeHint().height())

    def _width_for(self, text: str) -> int:
        """Wide enough to show the whole name, capped to the screen."""
        needed = self._edit.fontMetrics().horizontalAdvance(text) + self._MARGIN
        screen = self.screen() or QApplication.primaryScreen()
        cap = int(screen.availableGeometry().width() * 0.9) if screen else 1400
        return max(self._MIN_WIDTH, min(needed, cap))

    def new_name(self) -> str:
        return self._edit.text().strip()


class FilePaneWidget(QWidget):
    """Self-contained file pane with navigation, filter, CRUD."""

    dir_changed = pyqtSignal(str)
    open_terminal_requested = pyqtSignal(str)
    filesystem_changed = pyqtSignal()
    clipboard_changed = pyqtSignal()

    # Shared clipboard across all pane instances
    _clipboard_paths: list[str] = []
    _clipboard_mode: str = "copy"   # "copy" or "cut"

    def __init__(self, parent=None, start_dir: str | None = None, state_manager=None, vcs_manager=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._forward: list[str] = []
        self._tabs: list[dict] = []
        self._loading_tab = False
        self._transfer_worker = None
        self._du_workers: set = set()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_watched_dir_changed)
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.timeout.connect(self._apply_watched_change)
        self._state = state_manager
        self._vcs = vcs_manager
        self.current_dir = (
            os.path.expanduser("~") if start_dir is None
            else os.path.abspath(os.path.expanduser(start_dir))
        )

        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 2, 2)
        v.setSpacing(2)

        self._tabbar = QTabBar()
        self._tabbar.setDocumentMode(True)
        self._tabbar.setExpanding(False)
        self._tabbar.setMovable(True)
        self._tabbar.setTabsClosable(True)
        self._tabbar.currentChanged.connect(self._on_tab_changed)
        self._tabbar.tabMoved.connect(self._on_tab_moved)
        self._tabbar.tabCloseRequested.connect(self.close_tab)
        v.addWidget(self._tabbar)

        # ── Navigation bar ────────────────────────────────────────
        nav = QHBoxLayout()

        def _nav_btn(label, tip, slot):
            b = QPushButton(label)
            b.setFixedWidth(32)
            b.setToolTip(tip)
            b.setToolTipDuration(3000)
            b.clicked.connect(slot)
            return b

        nav.addWidget(_nav_btn("←", "Go Back  (Alt+Left)",              self.go_back))
        nav.addWidget(_nav_btn("→", "Go Forward  (Alt+Right)",          self.go_forward))
        nav.addWidget(_nav_btn("↑", "Parent Directory  (Alt+Up)",        self.go_up))
        nav.addWidget(_nav_btn("⌂", "Home Directory  (Alt+Home)",
                               lambda: self.navigate(os.path.expanduser("~"))))

        self._addr = _PathLineEdit()
        self._addr.setToolTip("Address bar — type a path and press Enter to navigate; Tab completes paths")
        self._addr.navigate_requested.connect(self._navigate_from_address)
        self._addr.tab_complete_requested.connect(self._complete_address_path)

        self._addr_fs_model = QFileSystemModel(self._addr)
        self._addr_fs_model.setRootPath("")
        self._addr_completer = QCompleter(self._addr_fs_model, self._addr)
        self._addr_completer.setCompletionMode(QCompleter.CompletionMode.InlineCompletion)
        self._addr_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseSensitive)
        self._addr.setCompleter(self._addr_completer)
        # Install this after QCompleter so our filter runs first. QCompleter's
        # own filter can otherwise swallow Return/Tab before _PathLineEdit gets
        # either key event.
        self._addr.installEventFilter(self)

        nav.addWidget(self._addr, 1)

        # Hide/show tree toggle
        self._btn_tree = QPushButton("⊡ Tree")
        self._btn_tree.setToolTip("Toggle directory tree panel  (Ctrl+\\)")
        self._btn_tree.setToolTipDuration(3000)
        self._btn_tree.setCheckable(True)
        self._btn_tree.setChecked(True)
        self._btn_tree.setFixedWidth(64)
        self._btn_tree.toggled.connect(self._on_tree_toggle)
        nav.addWidget(self._btn_tree)

        v.addLayout(nav)

        # ── Filter bar ────────────────────────────────────────────
        flt = QHBoxLayout()
        flt.addWidget(QLabel("Filter:"))

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("type to filter…")
        self._filter.textChanged.connect(self._on_filter)

        btn_clr = QPushButton("✕")
        btn_clr.setFixedWidth(24)
        btn_clr.setToolTip("Clear filter")
        btn_clr.clicked.connect(self._filter.clear)

        flt.addWidget(self._filter, 1)
        flt.addWidget(btn_clr)
        v.addLayout(flt)

        # ── Splitter: tree | file list ────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = TreePanel()
        self.tree.dir_selected.connect(self.navigate)
        self.tree.device_connection_failed.connect(
            lambda message: QMessageBox.warning(self, "Connect device", message)
        )

        self.file_list = FileListPane()
        self.file_list.navigated.connect(self._on_list_navigated)
        self.file_list.file_activated.connect(self._open_file)
        self.file_list.paths_dropped.connect(self._on_paths_dropped)

        # Right-click on file list rows
        self.file_list.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.table.customContextMenuRequested.connect(self._file_context_menu)

        self._splitter.addWidget(self.tree)
        self._splitter.addWidget(self.file_list)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setSizes([220, 660])

        v.addWidget(self._splitter, 1)

        if self._vcs is not None:
            self.file_list.set_vcs_manager(self._vcs)
            self.tree.set_vcs_manager(self._vcs)
            self._vcs.repo_status_changed.connect(self._on_repo_status_changed)
            self.dir_changed.connect(self._track_dir)

        self._tabs.append({"path": self.current_dir, "back": [], "forward": []})
        self._tabbar.addTab(self._tab_label(self.current_dir))
        self._tabbar.setCurrentIndex(0)
        self._load_dir(self.current_dir)

    # ── Git status wiring ─────────────────────────────────────────

    def _track_dir(self, path: str):
        self._vcs.ensure_tracking(path, watcher_owner=self)

    def _on_repo_status_changed(self, repo_root: str):
        if self._vcs is None:
            return
        if self._vcs.repo_root_for(self.current_dir) == repo_root:
            self.file_list.notify_status_changed()
            self.tree.notify_status_changed()

    def current_repo_root(self) -> str | None:
        return self._vcs.repo_root_for(self.current_dir) if self._vcs else None

    def refresh_git_status(self):
        """Explicit refresh (e.g. Ctrl+R), bypassing debounce/cache."""
        root = self.current_repo_root()
        if root is not None:
            self._vcs.invalidate(root)

    # ── Active pane indicator ─────────────────────────────────────

    def set_active(self, active: bool):
        if active:
            glow = QGraphicsDropShadowEffect(self)
            glow.setColor(QColor("#4a9eff"))
            glow.setBlurRadius(18)
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        else:
            self.setGraphicsEffect(None)

    # ── Tree toggle ───────────────────────────────────────────────

    def _on_tree_toggle(self, checked: bool):
        self.tree.setVisible(checked)

    def set_tree_visible(self, visible: bool):
        self.tree.setVisible(visible)
        self._btn_tree.setChecked(visible)

    def tree_visible(self) -> bool:
        # Button state, not tree.isVisible(): a hidden pane hides the tree
        # widget even when the user has it toggled on.
        return self._btn_tree.isChecked()

    def get_splitter_sizes(self) -> list[int]:
        return self._splitter.sizes()

    def set_splitter_sizes(self, sizes: list[int]):
        # Sizes saved while this pane was hidden come back as zeros;
        # applying them collapses the pane. Keep the defaults instead.
        if len(sizes) == 2 and sizes[1] >= 50:
            self._splitter.setSizes(sizes)

    def ensure_sane_layout(self):
        sizes = self._splitter.sizes()
        if len(sizes) != 2 or sizes[1] < 50:
            self._splitter.setSizes([220, 660])

    # ── Navigation ────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self._addr:
            address_keys = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab)
            # Claim these keys before MainWindow's window-level shortcuts are
            # resolved. In particular, "Open Selected" is also bound to
            # Return and otherwise prevents the address bar seeing Enter.
            if (event.type() == QEvent.Type.ShortcutOverride
                    and event.key() in address_keys):
                event.accept()
                return True
            if (event.type() == QEvent.Type.KeyPress
                    and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
                self._navigate_from_address()
                event.accept()
                return True
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Tab:
                self._complete_address_path()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _tab_label(self, path: str) -> str:
        return os.path.basename(path) or path

    def _sync_current_tab(self):
        idx = self._tabbar.currentIndex()
        if 0 <= idx < len(self._tabs):
            self._tabs[idx] = {
                "path": self.current_dir,
                "back": list(self._history),
                "forward": list(self._forward),
            }
            self._tabbar.setTabText(idx, self._tab_label(self.current_dir))
            self._tabbar.setTabToolTip(idx, self.current_dir)

    def _load_dir(self, path: str):
        self.current_dir = path
        self.file_list.navigate(path)
        self.tree.navigate(path)
        self._addr.setText(path)
        self._sync_current_tab()
        self._watch_current_dir()
        self.dir_changed.emit(path)

    def _resolve_address_path(self, text: str) -> str:
        text = text.strip()
        if not text:
            return self.current_dir
        expanded = os.path.expanduser(text)
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.current_dir, expanded)
        return os.path.abspath(expanded)

    def _navigate_from_address(self):
        path = self._resolve_address_path(self._addr.text())
        if os.path.isdir(path):
            self.navigate(path)
            return
        self._addr.setText(path)
        self._addr.selectAll()
        QMessageBox.warning(self, "Navigate", f"Not a directory:\n{path}")

    def _complete_address_path(self):
        text = self._addr.text()
        cursor = self._addr.cursorPosition()
        prefix_text = text[:cursor]
        suffix_text = text[cursor:]

        resolved_prefix = os.path.expanduser(prefix_text)
        was_relative = not os.path.isabs(resolved_prefix)
        search_prefix = (
            os.path.join(self.current_dir, resolved_prefix)
            if was_relative else resolved_prefix
        )
        matches = sorted(glob.glob(search_prefix + "*"))
        if not matches:
            QApplication.beep()
            return

        common = os.path.commonprefix(matches) if len(matches) > 1 else matches[0]
        if len(matches) == 1 and os.path.isdir(matches[0]):
            common = matches[0] + os.sep

        display = os.path.relpath(common, self.current_dir) if was_relative else common
        if prefix_text.startswith("~"):
            home = os.path.expanduser("~")
            if common == home or common.startswith(home + os.sep):
                display = "~" + common[len(home):]
        if was_relative and display == ".":
            display = ""

        new_text = display + suffix_text
        self._addr.setText(new_text)
        self._addr.setCursorPosition(len(display))

    def navigate(self, path: str, add_history: bool = True):
        path = self._resolve_address_path(path)
        if not os.path.isdir(path):
            return
        if add_history and self.current_dir != path:
            self._history.append(self.current_dir)
            self._forward.clear()
            # A filter describes the old directory. Keeping it during
            # navigation can make a populated destination appear completely
            # empty, which looks like navigation failed.
            self._filter.clear()
        self._load_dir(path)

    def open_location(self, location: str):
        """Open a local directory or reconnect a stable SMB URI."""
        if location.casefold().startswith("smb://"):
            self.tree.connect_smb_uri(location)
        else:
            self.navigate(location)

    def bookmark_location(self) -> str:
        """Return a persistent location for the current directory."""
        return self.tree.smb_uri_for_path(self.current_dir) or self.current_dir

    def _on_list_navigated(self, path: str):
        self.navigate(path)

    def go_back(self):
        if self._history:
            prev = self._history.pop()
            self._forward.append(self.current_dir)
            self._load_dir(prev)

    def go_forward(self):
        if self._forward:
            nxt = self._forward.pop()
            self._history.append(self.current_dir)
            self._load_dir(nxt)

    def go_up(self):
        parent = os.path.dirname(self.current_dir)
        if parent != self.current_dir:
            self.navigate(parent)

    def new_tab(self, path: str | None = None):
        path = os.path.abspath(os.path.expanduser(path or self.current_dir))
        if not os.path.isdir(path):
            return
        self._sync_current_tab()
        self._tabs.append({"path": path, "back": [], "forward": []})
        self._tabbar.addTab(self._tab_label(path))
        idx = len(self._tabs) - 1
        self._tabbar.setTabToolTip(idx, path)
        # Do not rely on currentChanged to initialise the new tab.  Depending
        # on QTabBar signal timing (notably while tabs are being restored or
        # moved), that signal can be ignored by _loading_tab and leave the
        # shared file view displaying no directory for the active tab.
        self._loading_tab = True
        try:
            self._tabbar.setCurrentIndex(idx)
            self._history = []
            self._forward = []
            self._load_dir(path)
        finally:
            self._loading_tab = False

    def close_tab(self, index: int | None = None):
        if len(self._tabs) <= 1:
            return
        idx = self._tabbar.currentIndex() if index is None else index
        if not (0 <= idx < len(self._tabs)):
            return
        self._tabs.pop(idx)
        self._tabbar.removeTab(idx)
        new_idx = min(idx, len(self._tabs) - 1)
        self._tabbar.setCurrentIndex(new_idx)

    def cycle_tab(self, step: int = 1):
        count = self._tabbar.count()
        if count <= 1:
            return
        idx = (self._tabbar.currentIndex() + step) % count
        self._tabbar.setCurrentIndex(idx)

    def _on_tab_moved(self, old_index: int, new_index: int):
        """Keep the per-tab navigation state aligned with movable tab labels."""
        if old_index == new_index or not (0 <= old_index < len(self._tabs)):
            return
        tab = self._tabs.pop(old_index)
        self._tabs.insert(new_index, tab)

    def _on_tab_changed(self, index: int):
        if self._loading_tab or not (0 <= index < len(self._tabs)):
            return
        tab = self._tabs[index]
        path = tab.get("path", self.current_dir)
        if not os.path.isdir(path):
            path = os.path.expanduser("~")
        self._history = list(tab.get("back", []))
        self._forward = list(tab.get("forward", []))
        self._loading_tab = True
        try:
            self._load_dir(path)
        finally:
            self._loading_tab = False

    def tabs_state(self) -> dict:
        self._sync_current_tab()
        return {
            "current": self._tabbar.currentIndex(),
            "tabs": list(self._tabs),
        }

    def set_tabs_state(self, state: dict):
        tabs = state.get("tabs") if isinstance(state, dict) else None
        if not isinstance(tabs, list) or not tabs:
            return
        valid_tabs = []
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            path = os.path.abspath(os.path.expanduser(tab.get("path", "")))
            if os.path.isdir(path):
                valid_tabs.append({
                    "path": path,
                    "back": [p for p in tab.get("back", []) if isinstance(p, str)],
                    "forward": [p for p in tab.get("forward", []) if isinstance(p, str)],
                })
        if not valid_tabs:
            return
        self._loading_tab = True
        try:
            self._tabs = valid_tabs
            while self._tabbar.count():
                self._tabbar.removeTab(0)
            for tab in self._tabs:
                idx = self._tabbar.addTab(self._tab_label(tab["path"]))
                self._tabbar.setTabToolTip(idx, tab["path"])
            current = state.get("current", 0)
            if not isinstance(current, int) or not (0 <= current < len(self._tabs)):
                current = 0
            self._tabbar.setCurrentIndex(current)
            tab = self._tabs[current]
            self._history = list(tab.get("back", []))
            self._forward = list(tab.get("forward", []))
            self._load_dir(tab["path"])
        finally:
            self._loading_tab = False

    def _on_filter(self, text: str):
        self.file_list.set_filter(text)

    def refresh(self):
        self._refresh_views()
        self.refresh_git_status()

    def _refresh_views(self, tree_path: str | None = None):
        self.file_list.refresh()
        self.tree.refresh(tree_path or self.current_dir)

    # ── Live directory watching ───────────────────────────────────

    # Coalesce bursts: another app copying 100 files emits ~100 events, and
    # reloading once per event would thrash a directory listing.
    _WATCH_DEBOUNCE_MS = 250

    @staticmethod
    def is_watchable(path: str) -> bool:
        """False for gvfs mounts (phones, SMB).

        Those are FUSE layers over protocols with no change notification, so
        inotify never delivers anything: the watch would sit there looking
        live while showing stale contents. Better to not claim to watch them.
        """
        return "/gvfs/" not in path and not path.startswith("/run/user/")

    def _watch_current_dir(self):
        """Point the watcher at the directory now on screen."""
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        if self.is_watchable(self.current_dir) and os.path.isdir(self.current_dir):
            self._watcher.addPath(self.current_dir)

    def _on_watched_dir_changed(self, _path: str):
        self._watch_timer.start(self._WATCH_DEBOUNCE_MS)

    def _apply_watched_change(self):
        # A rename or delete of the directory itself drops the watch; if it
        # is gone there is nothing to show, and navigation will re-arm.
        if not os.path.isdir(self.current_dir):
            return
        self.file_list.refresh()          # preserves selection and scroll
        if self.current_dir not in self._watcher.directories():
            self._watch_current_dir()     # some editors replace the dir inode

    def _on_paths_dropped(self, paths: list[str], dest_dir: str, action):
        if action == Qt.DropAction.MoveAction:
            self.move_paths_to(paths, dest_dir)
        else:
            self.copy_paths_to(paths, dest_dir)

    # ── Open file / directory ─────────────────────────────────────

    def _open_file(self, path: str):
        if self._state is not None:
            from src.open_with import detect_mime
            mime = detect_mime(path)
            default_cmd = self._state.get_default(mime)
            if default_cmd:
                try:
                    subprocess.Popen(default_cmd.split() + [path])
                    return
                except OSError:
                    pass  # fall through to xdg-open if default cmd is broken
        try:
            subprocess.Popen(["xdg-open", path])
        except OSError as e:
            QMessageBox.warning(self, "Open", str(e))

    def _open_with(self, path: str):
        from src.open_with import OpenWithDialog
        OpenWithDialog.launch(path, parent=self, state_manager=self._state)

    def open_selected(self):
        paths = self.file_list.selected_paths()
        if not paths:
            return
        for path in paths:
            if os.path.isdir(path):
                self.navigate(path)
                return
            self._open_file(path)

    def open_with_selected(self):
        paths = self.file_list.selected_paths()
        if paths and os.path.isfile(paths[0]):
            self._open_with(paths[0])

    def select_all(self):
        self.file_list.table.selectAll()

    def extract_selected_here(self):
        archives = [p for p in self.file_list.selected_paths() if self._is_archive(p)]
        if archives:
            self.extract_archives(archives)

    def extract_selected_to_folder(self):
        archives = [p for p in self.file_list.selected_paths() if self._is_archive(p)]
        if archives:
            self.extract_archives_to_folder(archives)

    def create_archive_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            self.create_archive(paths)

    def copy_paths_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            self.copy_paths(paths)

    def copy_checksum_selected(self):
        paths = self.file_list.selected_paths()
        if paths and os.path.isfile(paths[0]):
            self.copy_checksum(paths[0])

    def edit_permissions_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            self.edit_permissions(paths[0])

    def edit_owner_group_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            self.edit_owner_group(paths[0])

    def show_properties_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            self.show_properties(paths[0])

    # ── File list right-click context menu ────────────────────────

    def _shortcut_for(self, action_key: str) -> str:
        if self._state is not None:
            saved = self._state.get_hotkey(action_key)
            if saved is not None:
                return saved
        from src.settings import _DEFAULT_HOTKEYS
        return _DEFAULT_HOTKEYS.get(action_key, "")

    def _menu_action(self, menu: QMenu, label: str, action_key: str, slot, enabled: bool = True) -> QAction:
        action = QAction(label, menu)
        shortcut = self._shortcut_for(action_key)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
            if hasattr(action, "setShortcutVisibleInContextMenu"):
                action.setShortcutVisibleInContextMenu(True)
        action.setEnabled(enabled)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _file_context_menu(self, pos):
        table = self.file_list.table
        viewport = self.file_list.table.viewport()
        global_pos = viewport.mapToGlobal(pos)
        idx = table.indexAt(pos)

        if idx.isValid():
            if not table.selectionModel().isRowSelected(idx.row(), idx.parent()):
                table.selectRow(idx.row())
            paths = self.file_list.selected_paths()
        else:
            table.clearSelection()
            paths = []

        menu = QMenu(self)

        if not paths:
            self._menu_action(menu, "New Folder", "new_folder", self.new_folder)
            self._menu_action(menu, "New File", "new_file", self.new_file)
            self._menu_action(menu, "New Tab Here", "new_tab", lambda: self.new_tab())
            menu.addSeparator()
            if FilePaneWidget._clipboard_paths:
                self._menu_action(menu, "Paste", "paste", self.paste)
            self._menu_action(menu, "Extract Archive Here…", "extract_archive_here",
                              self.extract_archive_here)
            menu.addSeparator()
            self._menu_action(menu, "Open Terminal Here", "open_terminal",
                              lambda: self.open_terminal_requested.emit(self.current_dir))
            self._menu_action(menu, "Refresh", "refresh", self.refresh)
            self._menu_action(menu, "Select All", "select_all", self.select_all)
            menu.exec(global_pos)
            return

        is_single = (len(paths) == 1)
        path = paths[0]
        is_dir = os.path.isdir(path)

        # Open / Open With (single selection only)
        if is_single:
            label = f'Open "{os.path.basename(path)}"'
            if is_dir:
                self._menu_action(menu, label, "open_selected", lambda: self.navigate(path))
            else:
                self._menu_action(menu, label, "open_selected", lambda: self._open_file(path))
                self._menu_action(menu, "Open With…", "open_with", lambda: self._open_with(path))
            menu.addSeparator()

        # Edit operations
        self._menu_action(menu, "Cut", "cut", self.cut_selected)
        self._menu_action(menu, "Copy", "copy", self.copy_selected)
        if FilePaneWidget._clipboard_paths:
            self._menu_action(menu, "Paste", "paste", self.paste)

        menu.addSeparator()
        self._menu_action(menu, "Duplicate", "duplicate", self.duplicate_selected)
        if is_single:
            self._menu_action(menu, "Rename", "rename", self.rename_selected)
        self._menu_action(menu, "Delete", "delete", self.delete_selected)

        archive_paths = [p for p in paths if self._is_archive(p)]
        if archive_paths:
            menu.addSeparator()
            self._menu_action(menu, "Extract Here", "extract_here",
                              lambda: self.extract_archives(archive_paths))
            self._menu_action(menu, "Extract to Folder…", "extract_to_folder",
                              lambda: self.extract_archives_to_folder(archive_paths))

        menu.addSeparator()
        self._menu_action(menu, "Create Archive…", "create_archive",
                          lambda: self.create_archive(paths))

        # Get Size (dirs only)
        dir_paths = [p for p in paths if os.path.isdir(p)]
        if dir_paths:
            menu.addSeparator()
            self._menu_action(menu, "Get Size", "get_size", self.get_size_selected)

        if is_single and os.path.isfile(path):
            self._menu_action(menu, "Copy SHA-256", "copy_checksum",
                              lambda: self.copy_checksum(path))

        menu.addSeparator()
        self._menu_action(menu, "Copy Path", "copy_path", lambda: self.copy_paths(paths))
        if is_single:
            self._menu_action(menu, "Permissions…", "permissions",
                              lambda: self.edit_permissions(path))
            self._menu_action(menu, "Owner / Group…", "owner_group",
                              lambda: self.edit_owner_group(path))
            self._menu_action(menu, "Properties", "properties",
                              lambda: self.show_properties(path))

        menu.exec(global_pos)

    # ── Archive extraction ────────────────────────────────────────

    @staticmethod
    def _unpack(archive_path: str, dest: str):
        """Extract with tar members constrained to `dest`.

        A tar entry may name `../../etc/cron.d/x` or a symlink pointing out
        of the tree, and plain `unpack_archive` on Python 3.12 still honours
        it. `filter="data"` refuses those. Zip members are already sanitised
        by zipfile itself, and older Pythons reject the keyword, so fall
        back rather than fail.
        """
        try:
            shutil.unpack_archive(archive_path, dest, filter="data")
        except TypeError:
            shutil.unpack_archive(archive_path, dest)

    @staticmethod
    def _is_archive(path: str) -> bool:
        name = path.lower()
        return os.path.isfile(path) and name.endswith((
            ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"
        ))

    def extract_archive_here(self):
        filters = "Archives (*.zip *.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz);;All files (*)"
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self, "Extract Archive Here", self.current_dir, filters
        )
        if paths:
            self.extract_archives(paths)

    def extract_archives(self, archive_paths: list[str]):
        for archive_path in archive_paths:
            try:
                self._unpack(archive_path, self.current_dir)
            except (shutil.ReadError, OSError, ValueError, tarfile.TarError) as e:
                QMessageBox.warning(self, "Extract", f"{os.path.basename(archive_path)}: {e}")
        self._refresh_views(self.current_dir)
        self.filesystem_changed.emit()

    def extract_archives_to_folder(self, archive_paths: list[str]):
        for archive_path in archive_paths:
            default_name = os.path.splitext(os.path.basename(archive_path))[0]
            if default_name.endswith(".tar"):
                default_name = default_name[:-4]
            folder, ok = QInputDialog.getText(
                self, "Extract to Folder", "Folder name:", text=default_name
            )
            if not ok or not folder:
                continue
            dest = self._unique_path(os.path.join(self.current_dir, folder))
            try:
                os.makedirs(dest, exist_ok=False)
                self._unpack(archive_path, dest)
            except (shutil.ReadError, OSError, ValueError, tarfile.TarError) as e:
                QMessageBox.warning(self, "Extract", f"{os.path.basename(archive_path)}: {e}")
        self._refresh_views(self.current_dir)
        self.filesystem_changed.emit()

    def create_archive(self, paths: list[str]):
        if not paths:
            return
        base = os.path.splitext(os.path.basename(paths[0]))[0] if len(paths) == 1 else "archive"
        target, selected = QFileDialog.getSaveFileName(
            self,
            "Create Archive",
            os.path.join(self.current_dir, f"{base}.zip"),
            "Zip archive (*.zip);;Tar gzip archive (*.tar.gz);;Tar archive (*.tar)",
        )
        if not target:
            return
        fmt = "zip"
        if selected.startswith("Tar gzip") or target.endswith((".tar.gz", ".tgz")):
            fmt = "gztar"
            if target.endswith(".tar.gz"):
                target = target[:-7]
            elif target.endswith(".tgz"):
                target = target[:-4]
        elif selected.startswith("Tar") or target.endswith(".tar"):
            fmt = "tar"
            if target.endswith(".tar"):
                target = target[:-4]
        elif target.endswith(".zip"):
            target = target[:-4]
        try:
            if len(paths) == 1:
                src = paths[0]
                shutil.make_archive(target, fmt, os.path.dirname(src), os.path.basename(src))
            else:
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    for src in paths:
                        dst = os.path.join(tmp, os.path.basename(src))
                        if os.path.isdir(src):
                            shutil.copytree(src, dst)
                        else:
                            shutil.copy2(src, dst)
                    shutil.make_archive(target, fmt, tmp)
        except OSError as e:
            QMessageBox.warning(self, "Create Archive", str(e))
        self._refresh_views(self.current_dir)
        self.filesystem_changed.emit()

    # ── Get Size ──────────────────────────────────────────────────

    def get_size_selected(self):
        """Run du -sb on all selected directories; write results into the Size column."""
        paths = [p for p in self.file_list.selected_paths() if os.path.isdir(p)]
        if not paths:
            return
        # Held in a set, not a single attribute: `du` on a large tree runs for
        # a while, and reassigning one attribute dropped the previous worker
        # while its thread was still going, which aborts Qt with
        # "QThread: Destroyed while thread is still running".
        worker = _DuWorker(paths)
        worker.result.connect(self.file_list.set_dir_size)
        worker.finished.connect(lambda w=worker: self._du_workers.discard(w))
        self._du_workers.add(worker)
        worker.start()

    # ── Clipboard (shared across panes) ───────────────────────────

    def copy_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            FilePaneWidget._clipboard_paths = paths
            FilePaneWidget._clipboard_mode  = "copy"
            self.clipboard_changed.emit()

    def cut_selected(self):
        paths = self.file_list.selected_paths()
        if paths:
            FilePaneWidget._clipboard_paths = paths
            FilePaneWidget._clipboard_mode  = "cut"
            self.clipboard_changed.emit()

    def paste(self):
        if not FilePaneWidget._clipboard_paths:
            return
        mode = FilePaneWidget._clipboard_mode
        if mode == "cut":
            self.move_paths_to(FilePaneWidget._clipboard_paths, self.current_dir)
        else:
            self.copy_paths_to(FilePaneWidget._clipboard_paths, self.current_dir)
        if mode == "cut":
            FilePaneWidget._clipboard_paths = []
            self.clipboard_changed.emit()
        self._refresh_views(self.current_dir)
        self.filesystem_changed.emit()

    @classmethod
    def current_cut_paths(cls) -> set[str]:
        if cls._clipboard_mode != "cut":
            return set()
        return {os.path.abspath(path) for path in cls._clipboard_paths}

    @staticmethod
    def _unique_path(path: str) -> str:
        if not os.path.exists(path):
            return path
        directory = os.path.dirname(path)
        name = os.path.basename(path)
        stem, ext = os.path.splitext(name)
        counter = 1
        while True:
            candidate = os.path.join(directory, f"{stem} ({counter}){ext}")
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    def copy_paths_to(self, paths: list[str], dest_dir: str):
        pairs = [
            (src, self._unique_path(os.path.join(dest_dir, os.path.basename(src))))
            for src in paths if os.path.exists(src)
        ]
        self._start_transfer(pairs, move=False, dest_dir=dest_dir)

    def move_paths_to(self, paths: list[str], dest_dir: str):
        pairs = []
        for src in paths:
            if not os.path.exists(src):
                continue
            if os.path.abspath(os.path.dirname(src)) == os.path.abspath(dest_dir):
                continue
            pairs.append(
                (src, self._unique_path(os.path.join(dest_dir, os.path.basename(src))))
            )
        self._start_transfer(pairs, move=True, dest_dir=dest_dir)

    # Reading every file back to checksum it roughly doubles transfer time,
    # so a plain copy skips it — the original still exists, so a bad copy
    # costs a retry. A move is different: it deletes the source, and on MTP
    # nothing else can tell us the bytes really landed, so an unverified
    # move risks losing the only copy. Same-filesystem moves are renames and
    # never reach this path, so in practice this only slows moves onto a
    # phone or network share, which is exactly where the risk is.
    VERIFY_COPIES = False
    VERIFY_MOVES = True

    def _start_transfer(self, pairs: list[tuple[str, str]], move: bool, dest_dir: str):
        """Run a copy/move on a worker thread behind a cancellable progress dialog."""
        if not pairs:
            return
        title = "Moving" if move else "Copying"
        verify = self.VERIFY_MOVES if move else self.VERIFY_COPIES
        passes = 2 if verify else 1   # copy, plus readback when verifying

        dlg = QProgressDialog(f"{title}…", "Cancel", 0, 0, self)   # 0,0 = busy
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(400)   # stay out of the way for quick local copies
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumWidth(460)      # room for the speed/ETA line without reflow
        # Qt only starts the minimumDuration "show after 400ms" timer once
        # setValue() has been called at least once. A copy onto an MTP device
        # goes through one blocking `gio copy` call per file that reports
        # nothing until the file is entirely done (see copy_file_gio), so
        # without this the dialog would never appear for a slow device copy.
        dlg.setValue(0)

        worker = TransferWorker(pairs, move=move, verify=verify)
        errors: list[tuple[str, str]] = []
        started = time.monotonic()
        total = 0

        def on_totals(total_bytes, _total_files):
            nonlocal total
            total = total_bytes
            dlg.setRange(0, 100)      # sizes known — switch to a real bar

        def on_progress(copied, verified, name, files_done, files_total, phase):
            # The bar spans both passes: writing the bytes and reading them
            # back. It reaches 100% only once the copy is verified, so it
            # never claims done while data may still be in flight.
            verifying = (phase == TransferWorker.PHASE_VERIFY)
            work_done = copied + verified
            work_total = total * passes
            if total > 0:
                dlg.setValue(min(100, int(work_done * 100 / work_total)))
            elapsed = time.monotonic() - started
            done_bytes = verified if verifying else copied
            speed = work_done / elapsed if elapsed > 0 else 0
            remaining = max(0, files_total - files_done)
            eta = ""
            if speed > 0 and work_total > work_done:
                secs = int((work_total - work_done) / speed)
                eta = f"  ·  {_fmt_duration(secs)} left"
            action = "Verifying" if verifying else title
            dlg.setLabelText(
                f"{action} {name}\n"
                f"{_fmt_bytes(done_bytes)} of {_fmt_bytes(total)}  ·  "
                f"{_fmt_bytes(speed)}/s{eta}\n"
                + (f"verifying {remaining} file(s) remaining"
                   if verifying else f"{remaining} file(s) remaining")
            )

        def on_done(cancelled):
            dlg.close()
            self._refresh_views(
                dest_dir if dest_dir == self.current_dir else self.current_dir
            )
            self.filesystem_changed.emit()
            if errors:
                detail = "\n".join(f"{p}: {msg}" for p, msg in errors[:20])
                if len(errors) > 20:
                    detail += f"\n… and {len(errors) - 20} more"
                QMessageBox.warning(
                    self, title, f"{len(errors)} item(s) failed:\n\n{detail}"
                )

        worker.totals.connect(on_totals)
        worker.progress.connect(on_progress)
        worker.failed.connect(lambda p, m: errors.append((p, m)))
        worker.done.connect(on_done)
        dlg.canceled.connect(worker.cancel)
        # done fires from inside run(); the thread is still alive at that
        # point, so the reference must be held until finished, or Qt aborts
        # with "QThread: Destroyed while thread is still running".
        worker.finished.connect(self._clear_transfer_worker)

        self._transfer_worker = worker   # keep a ref; a GC'd QThread crashes Qt
        worker.start()

    def _clear_transfer_worker(self):
        self._transfer_worker = None

    # ── CRUD ──────────────────────────────────────────────────────

    def new_folder(self):
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="new_folder")
        if ok and name:
            path = os.path.join(self.current_dir, name)
            try:
                os.makedirs(path, exist_ok=True)
                self._refresh_views(self.current_dir)
                self.filesystem_changed.emit()
            except OSError as e:
                QMessageBox.warning(self, "Error", str(e))

    def new_file(self):
        name, ok = QInputDialog.getText(self, "New File",
                                        "File name (with extension):", text="new_file.txt")
        if ok and name:
            path = os.path.join(self.current_dir, name)
            if os.path.exists(path):
                QMessageBox.warning(self, "Error", f"{name} already exists.")
                return
            try:
                open(path, "wb").close()
                self._refresh_views(self.current_dir)
                self.filesystem_changed.emit()
            except OSError as e:
                QMessageBox.warning(self, "Error", str(e))

    def duplicate_selected(self):
        paths = self.file_list.selected_paths()
        if not paths:
            return
        self.copy_paths_to(paths, self.current_dir)

    def copy_paths(self, paths: list[str]):
        QApplication.clipboard().setText("\n".join(paths))

    def copy_checksum(self, path: str):
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            QApplication.clipboard().setText(h.hexdigest())
        except OSError as e:
            QMessageBox.warning(self, "Checksum", str(e))

    def edit_permissions(self, path: str):
        try:
            current = stat.S_IMODE(os.stat(path).st_mode)
        except OSError as e:
            QMessageBox.warning(self, "Permissions", str(e))
            return
        text, ok = QInputDialog.getText(
            self, "Permissions", "Octal mode:", text=f"{current:04o}"
        )
        if not ok or not text:
            return
        try:
            os.chmod(path, int(text, 8))
            self._refresh_views(os.path.dirname(path))
            self.filesystem_changed.emit()
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Permissions", str(e))

    def edit_owner_group(self, path: str):
        try:
            s = os.stat(path)
            import pwd
            import grp
            owner = pwd.getpwuid(s.st_uid).pw_name
            group = grp.getgrgid(s.st_gid).gr_name
        except Exception:
            owner = ""
            group = ""
        text, ok = QInputDialog.getText(
            self, "Owner / Group", "owner:group:", text=f"{owner}:{group}"
        )
        if not ok or ":" not in text:
            return
        owner_text, group_text = (part.strip() or None for part in text.split(":", 1))
        try:
            shutil.chown(path, user=owner_text, group=group_text)
            self._refresh_views(os.path.dirname(path))
            self.filesystem_changed.emit()
        except OSError as e:
            QMessageBox.warning(self, "Owner / Group", str(e))

    def show_properties(self, path: str):
        try:
            s = os.stat(path)
            kind = "Directory" if os.path.isdir(path) else "File"
            info = [
                f"Name: {os.path.basename(path) or path}",
                f"Path: {path}",
                f"Type: {kind}",
                f"Size: {s.st_size} bytes",
                f"Modified: {s.st_mtime}",
                f"Created: {getattr(s, 'st_birthtime', s.st_ctime)}",
                f"Permissions: {stat.filemode(s.st_mode)} ({stat.S_IMODE(s.st_mode):04o})",
                f"Owner UID: {s.st_uid}",
                f"Group GID: {s.st_gid}",
            ]
            QMessageBox.information(self, "Properties", "\n".join(info))
        except OSError as e:
            QMessageBox.warning(self, "Properties", str(e))

    def rename_selected(self):
        paths = self.file_list.selected_paths()
        if not paths:
            return
        src = paths[0]
        old_name = os.path.basename(src)
        dlg = _RenameDialog(old_name, self)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        new_name = dlg.new_name()
        if ok and new_name and new_name != old_name:
            dst = os.path.join(os.path.dirname(src), new_name)
            try:
                shutil.move(src, dst)
                self._refresh_views(os.path.dirname(dst))
                self.filesystem_changed.emit()
            except OSError as e:
                QMessageBox.warning(self, "Error", str(e))

    def delete_selected(self, confirm: bool = True):
        paths = self.file_list.selected_paths()
        if not paths:
            return
        if confirm:
            names = ", ".join(os.path.basename(p) for p in paths)
            reply = QMessageBox.question(
                self, "Delete", f"Delete: {names}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        for p in paths:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except OSError as e:
                QMessageBox.warning(self, "Error", str(e))
        self._refresh_views(self.current_dir)
        self.filesystem_changed.emit()

    def copy_to(self, dest_dir: str):
        """Cross-pane copy (F5)."""
        self.copy_paths_to(self.file_list.selected_paths(), dest_dir)

    def move_to(self, dest_dir: str):
        """Cross-pane move (F6)."""
        self.move_paths_to(self.file_list.selected_paths(), dest_dir)


# Alias so tests can import either name
FilePane = FilePaneWidget
