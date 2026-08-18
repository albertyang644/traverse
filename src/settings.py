"""Settings dialog: General, Hotkeys, Bash Actions."""

import subprocess

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QCheckBox, QDialogButtonBox, QKeySequenceEdit,
    QFontComboBox, QSpinBox,
)
from PyQt6.QtGui import QKeySequence, QFont
from PyQt6.QtCore import Qt


_DEFAULT_HOTKEYS = {
    "new_folder":      "Ctrl+Shift+N",
    "new_file":        "Ctrl+N",
    "new_tab":         "Ctrl+T",
    "close_tab":       "Ctrl+W",
    "next_tab":        "Ctrl+Tab",
    "prev_tab":        "Ctrl+Shift+Tab",
    "open_selected":   "Return",
    "open_with":       "",
    "cut":             "Ctrl+X",
    "copy":            "Ctrl+C",
    "paste":           "Ctrl+V",
    "duplicate":       "",
    "rename":          "F2",
    "delete":          "Delete",
    "select_all":      "Ctrl+A",
    "refresh":         "Ctrl+R",
    "open_terminal":   "F4",
    "extract_here":    "",
    "extract_to_folder": "",
    "extract_archive_here": "",
    "create_archive":  "",
    "copy_path":       "Ctrl+Shift+C",
    "copy_checksum":   "",
    "permissions":     "",
    "owner_group":     "",
    "properties":      "Alt+Return",
    "toggle_dual":     "F3",
    "search":          "Ctrl+F",
    "add_bookmark":    "Ctrl+B",
    "copy_to_other":   "F5",
    "move_to_other":   "F6",
    "go_back":         "Alt+Left",
    "go_forward":      "Alt+Right",
    "go_up":           "Alt+Up",
    "go_home":         "Alt+Home",
    "get_size":        "Ctrl+D",
}


class SettingsDialog(QDialog):
    """Modal settings with General / Hotkeys / Bash Actions tabs."""

    def __init__(self, state_manager, parent=None):
        super().__init__(parent)
        self._state = state_manager
        self.setWindowTitle("Settings — Traverse")
        self.setMinimumSize(640, 440)
        self.resize(700, 500)

        v = QVBoxLayout(self)
        tabs = QTabWidget()

        tabs.addTab(self._make_general_tab(), "General")
        tabs.addTab(self._make_fonts_tab(),   "Fonts")
        tabs.addTab(self._make_hotkeys_tab(), "Hotkeys")
        tabs.addTab(self._make_bash_tab(),    "Bash Actions")

        v.addWidget(tabs)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self._on_ok)
        bbox.rejected.connect(self.reject)
        v.addWidget(bbox)

    # ── General ──────────────────────────────────────────────────

    def _make_general_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        self._confirm_delete = QCheckBox("Confirm before deleting files")
        self._confirm_delete.setChecked(True)
        v.addWidget(self._confirm_delete)
        v.addWidget(QLabel("More options coming in a future release."))
        v.addStretch()
        return w

    # ── Fonts ────────────────────────────────────────────────────

    def _make_fonts_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        saved = self._state.get_font()

        # Family picker
        family_row = QHBoxLayout()
        family_row.addWidget(QLabel("Font:"))
        self._font_combo = QFontComboBox()
        if saved.get("family"):
            self._font_combo.setCurrentFont(QFont(saved["family"]))
        family_row.addWidget(self._font_combo, 1)
        v.addLayout(family_row)

        # Size picker
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:"))
        self._font_size = QSpinBox()
        self._font_size.setRange(6, 72)
        self._font_size.setSuffix(" pt")
        self._font_size.setValue(saved.get("size") or QFont().pointSize() or 10)
        size_row.addWidget(self._font_size)
        size_row.addStretch()
        v.addLayout(size_row)

        # Live preview
        v.addSpacing(12)
        v.addWidget(QLabel("Preview:"))
        self._font_preview = QLabel("The quick brown fox jumps over the lazy dog  0123456789")
        self._font_preview.setWordWrap(True)
        self._font_preview.setFrameStyle(1)   # plain box
        self._font_preview.setMinimumHeight(60)
        v.addWidget(self._font_preview)

        self._font_combo.currentFontChanged.connect(self._update_font_preview)
        self._font_size.valueChanged.connect(self._update_font_preview)
        self._update_font_preview()

        v.addStretch()
        return w

    def _update_font_preview(self):
        f = QFont(self._font_combo.currentFont().family(), self._font_size.value())
        self._font_preview.setFont(f)

    # ── Hotkeys ──────────────────────────────────────────────────

    def _make_hotkeys_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Click a key sequence, then press the shortcut you want to assign."
        ))

        self._hotkeys_tbl = QTableWidget()
        self._hotkeys_tbl.setColumnCount(2)
        self._hotkeys_tbl.setHorizontalHeaderLabels(["Action", "Key Sequence"])
        hdr = self._hotkeys_tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._hotkeys_tbl.setColumnWidth(1, 200)
        self._hotkeys_tbl.verticalHeader().setVisible(False)
        self._hotkeys_tbl.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        merged = dict(_DEFAULT_HOTKEYS)
        merged.update(self._state.get_hotkeys())

        self._hotkeys_tbl.setRowCount(len(merged))
        for row, (action, key) in enumerate(merged.items()):
            # Col 0 — non-editable action label
            label = action.replace("_", " ").title()
            name_item = QTableWidgetItem(label)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setData(Qt.ItemDataRole.UserRole, action)
            self._hotkeys_tbl.setItem(row, 0, name_item)

            # Col 1 — QKeySequenceEdit widget (click → press keys to capture)
            ks_edit = QKeySequenceEdit(QKeySequence(key))
            self._hotkeys_tbl.setCellWidget(row, 1, ks_edit)

        self._hotkeys_tbl.resizeRowsToContents()
        v.addWidget(self._hotkeys_tbl)
        return w

    # ── Bash Actions ─────────────────────────────────────────────

    def _make_bash_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Define shell commands to bind to hotkeys.\n"
            "Commands execute in the active pane's current directory."
        ))

        self._bash_tbl = QTableWidget()
        self._bash_tbl.setColumnCount(3)
        self._bash_tbl.setHorizontalHeaderLabels(["Name", "Command", "Hotkey"])
        hdr = self._bash_tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._bash_tbl.setColumnWidth(2, 160)
        self._bash_tbl.verticalHeader().setVisible(False)

        for ba in self._state.get_bash_actions():
            self._bash_add_row(ba.get("name", ""), ba.get("command", ""), ba.get("hotkey", ""))

        btns = QHBoxLayout()
        add_btn = QPushButton("Add row")
        add_btn.clicked.connect(lambda: self._bash_add_row())
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._bash_remove_row)
        btns.addWidget(add_btn)
        btns.addWidget(remove_btn)
        btns.addStretch()

        v.addWidget(self._bash_tbl)
        v.addLayout(btns)
        return w

    def _bash_add_row(self, name="", command="", hotkey=""):
        row = self._bash_tbl.rowCount()
        self._bash_tbl.insertRow(row)
        self._bash_tbl.setItem(row, 0, QTableWidgetItem(name))
        self._bash_tbl.setItem(row, 1, QTableWidgetItem(command))
        # Col 2 — QKeySequenceEdit for hotkey capture
        ks_edit = QKeySequenceEdit(QKeySequence(hotkey) if hotkey else QKeySequence())
        self._bash_tbl.setCellWidget(row, 2, ks_edit)

    def _bash_remove_row(self):
        row = self._bash_tbl.currentRow()
        if row >= 0:
            self._bash_tbl.removeRow(row)

    # ── OK ───────────────────────────────────────────────────────

    def _on_ok(self):
        # Save hotkeys
        for row in range(self._hotkeys_tbl.rowCount()):
            a_item = self._hotkeys_tbl.item(row, 0)
            ks_edit = self._hotkeys_tbl.cellWidget(row, 1)
            if a_item and ks_edit:
                action = a_item.data(Qt.ItemDataRole.UserRole)
                key_str = ks_edit.keySequence().toString()
                if action:
                    self._state.set_hotkey(action, key_str)

        # Save bash actions
        bash = []
        for row in range(self._bash_tbl.rowCount()):
            n = self._bash_tbl.item(row, 0)
            c = self._bash_tbl.item(row, 1)
            k = self._bash_tbl.cellWidget(row, 2)
            if c and c.text().strip():
                bash.append({
                    "name":    n.text() if n else "",
                    "command": c.text(),
                    "hotkey":  k.keySequence().toString() if k else "",
                })
        self._state.set_bash_actions(bash)

        # Save font
        self._state.set_font(
            self._font_combo.currentFont().family(),
            self._font_size.value(),
        )

        self._state.save()
        self.accept()
