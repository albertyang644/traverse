"""Open-With dialog — lists installed apps, filtered by MIME type."""

import configparser
import os
import re
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QDialogButtonBox, QPushButton,
    QSplitter, QGroupBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QFont


_EXEC_PLACEHOLDERS = re.compile(r'\s*%[fFuUdDnNickvm]\s*')

# Directories that may contain .desktop files (highest priority last in list,
# so we can deduplicate by name keeping the user-local version)
_APP_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
]


# ── Desktop-file parser ───────────────────────────────────────────────────────

class AppEntry:
    __slots__ = ("name", "exec_cmd", "icon", "mime_types", "terminal", "path")

    def __init__(self, name, exec_cmd, icon, mime_types, terminal, path):
        self.name       = name
        self.exec_cmd   = exec_cmd      # ready to use (placeholders stripped)
        self.icon       = icon
        self.mime_types = mime_types    # frozenset[str]
        self.terminal   = terminal      # bool — needs a terminal
        self.path       = path          # Path to .desktop file


def _parse_desktop(path: Path) -> AppEntry | None:
    cfg = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        cfg.read(str(path), encoding="utf-8")
    except Exception:
        return None
    if "Desktop Entry" not in cfg:
        return None
    s = cfg["Desktop Entry"]
    if s.get("Type", "Application") != "Application":
        return None
    if s.get("NoDisplay", "false").lower() == "true":
        return None
    if s.get("Hidden", "false").lower() == "true":
        return None
    name = s.get("Name", "").strip()
    exec_raw = s.get("Exec", "").strip()
    if not name or not exec_raw:
        return None
    exec_cmd = _EXEC_PLACEHOLDERS.sub(" ", exec_raw).strip()
    icon = s.get("Icon", "").strip()
    mime_raw = s.get("MimeType", "")
    mime_types = frozenset(m.strip() for m in mime_raw.split(";") if m.strip())
    terminal = s.get("Terminal", "false").lower() == "true"
    return AppEntry(name, exec_cmd, icon, mime_types, terminal, path)


def load_all_apps() -> list[AppEntry]:
    """Return all non-hidden Application entries, user overrides shadow system ones."""
    seen: dict[str, AppEntry] = {}   # name → entry (later dirs win)
    for app_dir in _APP_DIRS:
        if not app_dir.is_dir():
            continue
        for desktop_file in sorted(app_dir.glob("*.desktop")):
            entry = _parse_desktop(desktop_file)
            if entry:
                seen[entry.name.lower()] = entry
    return sorted(seen.values(), key=lambda e: e.name.lower())


_default_app_cache: dict[str, "AppEntry | None"] = {}


def default_app_for_mime(mime: str) -> "AppEntry | None":
    """Return the AppEntry set as the default launcher for *mime*, or None."""
    if mime in _default_app_cache:
        return _default_app_cache[mime]
    entry = None
    try:
        r = subprocess.run(
            ["xdg-mime", "query", "default", mime],
            capture_output=True, text=True, timeout=2,
        )
        desktop_name = r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        desktop_name = ""
    if desktop_name:
        for app_dir in _APP_DIRS:
            p = app_dir / desktop_name
            if p.is_file():
                entry = _parse_desktop(p)
                break
    _default_app_cache[mime] = entry
    return entry


def detect_mime(path: str) -> str:
    """Return MIME type for *path* using xdg-mime (falls back to mimetypes)."""
    try:
        r = subprocess.run(
            ["xdg-mime", "query", "filetype", path],
            capture_output=True, text=True, timeout=2,
        )
        mime = r.stdout.strip()
        if mime:
            return mime
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    import mimetypes
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


# ── Dialog ────────────────────────────────────────────────────────────────────

class OpenWithDialog(QDialog):
    """Pick an application to open *filepath* with."""

    def __init__(self, filepath: str, parent=None):
        super().__init__(parent)
        self._filepath = filepath
        self._chosen_cmd: str | None = None
        self._mime = detect_mime(filepath)

        self.setWindowTitle(f"Open With  —  {os.path.basename(filepath)}")
        self.setMinimumSize(480, 480)
        self.resize(520, 580)

        root = QVBoxLayout(self)

        # ── Info strip ────────────────────────────────────────────
        info = QLabel(f"<b>{os.path.basename(filepath)}</b>  <small>({self._mime})</small>")
        root.addWidget(info)

        # ── Search bar ────────────────────────────────────────────
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search applications…")
        self._search.textChanged.connect(self._filter)
        root.addWidget(self._search)

        # ── App list ──────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setIconSize(QSize(24, 24))
        self._list.itemDoubleClicked.connect(self._accept_item)
        self._list.currentItemChanged.connect(self._on_select)
        root.addWidget(self._list, 1)

        # ── Custom command ────────────────────────────────────────
        cmd_box = QGroupBox("Custom command")
        cmd_lay = QHBoxLayout(cmd_box)
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText("e.g.  gedit  or  /usr/bin/vlc")
        self._cmd_edit.textChanged.connect(self._on_cmd_typed)
        cmd_lay.addWidget(self._cmd_edit)
        root.addWidget(cmd_box)

        # ── Buttons ───────────────────────────────────────────────
        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = bbox.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setEnabled(False)
        bbox.accepted.connect(self._on_ok)
        bbox.rejected.connect(self.reject)
        root.addWidget(bbox)

        # ── Set as default checkbox ───────────────────────────────
        self._set_default_chk = QCheckBox(
            f'Always use this application for "{self._mime}" files'
        )
        root.addWidget(self._set_default_chk)

        # ── Populate ──────────────────────────────────────────────
        all_apps = load_all_apps()
        recommended = [a for a in all_apps if self._mime in a.mime_types]
        others      = [a for a in all_apps if self._mime not in a.mime_types]

        self._all_items: list[tuple[str, AppEntry]] = []  # (category, entry)

        if recommended:
            self._add_header("Recommended for this file type")
            for app in recommended:
                self._add_app(app, "recommended")

        self._add_header("All Applications")
        for app in others:
            self._add_app(app, "other")

    # ── Internal helpers ─────────────────────────────────────────

    def _add_header(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)   # not selectable
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(self._list.palette().mid())
        item.setData(Qt.ItemDataRole.UserRole, None)
        self._list.addItem(item)

    def _add_app(self, app: AppEntry, category: str):
        item = QListWidgetItem(app.name)
        # Themed icon
        if app.icon:
            if os.path.isabs(app.icon):
                icon = QIcon(app.icon)
            else:
                icon = QIcon.fromTheme(app.icon, QIcon.fromTheme("application-x-executable"))
            item.setIcon(icon)
        item.setData(Qt.ItemDataRole.UserRole, app)
        self._list.addItem(item)
        self._all_items.append((category, app))

    def _filter(self, text: str):
        text = text.lower()
        for i in range(self._list.count()):
            item = self._list.item(i)
            app = item.data(Qt.ItemDataRole.UserRole)
            if app is None:      # header row
                item.setHidden(False)
            else:
                item.setHidden(text not in app.name.lower())

    def _on_select(self, current, _prev):
        if current is None:
            return
        app = current.data(Qt.ItemDataRole.UserRole)
        if app:
            self._cmd_edit.setText(app.exec_cmd)
            self._ok_btn.setEnabled(True)

    def _on_cmd_typed(self, text: str):
        self._ok_btn.setEnabled(bool(text.strip()))
        # Deselect list if user is typing a custom command
        if text.strip():
            pass  # keep list selection for visual reference

    def _accept_item(self, item: QListWidgetItem):
        if item.data(Qt.ItemDataRole.UserRole):
            self._on_ok()

    def _on_ok(self):
        cmd = self._cmd_edit.text().strip()
        if cmd:
            self._chosen_cmd = cmd
            self.accept()

    # ── Public API ───────────────────────────────────────────────

    def chosen_command(self) -> str | None:
        """Returns the exec command (without the file path) or None if cancelled."""
        return self._chosen_cmd

    @property
    def set_as_default(self) -> bool:
        return self._set_default_chk.isChecked()

    @staticmethod
    def launch(filepath: str, parent=None, state_manager=None) -> bool:
        """Show the dialog and launch the chosen app. Returns True if launched."""
        dlg = OpenWithDialog(filepath, parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        cmd = dlg.chosen_command()
        if not cmd:
            return False
        if dlg.set_as_default and state_manager is not None:
            state_manager.set_default(dlg._mime, cmd)
            state_manager.save()
        try:
            subprocess.Popen(cmd.split() + [filepath])
            return True
        except OSError as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(parent, "Open With", f"Failed to launch:\n{e}")
            return False
