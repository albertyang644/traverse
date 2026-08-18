"""File list panel — QTableView backed by a proper QAbstractTableModel."""

import mimetypes
import os
import stat
from datetime import datetime
from functools import lru_cache

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTableView, QHeaderView, QAbstractItemView, QMenu,
    QApplication, QStyle, QRubberBand,
)
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, pyqtSignal,
    QEvent, QTimer, QUrl, QMimeData, QSize, QPoint, QRect, QItemSelectionModel,
    QThread, QItemSelection,
)
from PyQt6.QtGui import QColor, QBrush, QAction
from PyQt6.QtGui import QIcon

from src.vcs.status import FileStatus


# ── Column registry ───────────────────────────────────────────────────────────

_ALL_COLUMNS = ["Name", "Size", "Modified", "Created", "Type", "Extension", "Permissions", "Owner", "Group", "Git"]
(
    _COL_NAME, _COL_SIZE, _COL_MTIME, _COL_CTIME,
    _COL_TYPE, _COL_EXT,  _COL_PERMS, _COL_OWNER, _COL_GROUP, _COL_GIT,
) = range(len(_ALL_COLUMNS))

_DEFAULT_VISIBLE = {_COL_NAME, _COL_SIZE, _COL_MTIME, _COL_TYPE, _COL_GIT}

# Default widths (pixels) for each column
_DEFAULT_WIDTHS = {
    _COL_NAME:  300,
    _COL_SIZE:  80,
    _COL_MTIME: 140,
    _COL_CTIME: 140,
    _COL_TYPE:  70,
    _COL_EXT:   60,
    _COL_PERMS: 110,
    _COL_OWNER: 80,
    _COL_GROUP: 80,
    _COL_GIT:   90,
}

# FileStatus -> (label shown in the Git column, color used for the label and
# for tinting the Name column so a file's state is visible even when the
# Git column is hidden).
_GIT_DISPLAY = {
    FileStatus.CLEAN:       ("",           None),
    FileStatus.MODIFIED:    ("Modified",   "#e5a642"),
    FileStatus.ADDED:       ("Added",      "#5fb85f"),
    FileStatus.DELETED:     ("Deleted",    "#d95c5c"),
    FileStatus.RENAMED:     ("Renamed",    "#a67ee0"),
    FileStatus.UNTRACKED:   ("Untracked",  "#9a9a9a"),
    FileStatus.IGNORED:     ("Ignored",    "#6a6a6a"),
    FileStatus.CONFLICTED:  ("Conflicted", "#ff5555"),
    FileStatus.STAGED:      ("Staged",     "#4a9eff"),
}


# ── Directory scanning ───────────────────────────────────────────────────────

_SCAN_BATCH = 250   # rows per streamed batch


def _scan_dir(directory: str, on_batch=None) -> list[dict]:
    """Build the row dicts for `directory`.

    Uses scandir and stats each entry exactly once. On MTP/gvfs every stat is
    a USB round trip, so calling isdir() separately for a sort key (as an
    earlier version did) doubled the cost of opening a folder.

    If `on_batch` is given it is called with each chunk of rows as they are
    read, so a slow directory can populate the view progressively instead of
    showing nothing until the whole scan finishes.
    """
    rows: list[dict] = []
    batch: list[dict] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    s = entry.stat()
                    is_dir = stat.S_ISDIR(s.st_mode)
                    name = entry.name
                    row = {
                        "name":           name,
                        "size":           s.st_size,
                        "mtime":          s.st_mtime,
                        "ctime":          getattr(s, "st_birthtime", s.st_ctime),
                        "is_dir":         is_dir,
                        "path":           entry.path,
                        "ext":            "" if is_dir else os.path.splitext(name)[1].lstrip("."),
                        "perms":          stat.filemode(s.st_mode),
                        "owner":          _get_owner(s.st_uid),
                        "group":          _get_group(s.st_gid),
                        "dir_size_bytes": None,  # populated on demand by Get Size
                    }
                    rows.append(row)
                    if on_batch is not None:
                        batch.append(row)
                        if len(batch) >= _SCAN_BATCH:
                            on_batch(batch)
                            batch = []
                except OSError:
                    pass
    except OSError:
        pass
    if on_batch is not None and batch:
        on_batch(batch)
    rows.sort(key=lambda r: (not r["is_dir"], r["name"].lower()))
    return rows


class _ScanWorker(QThread):
    """Scans one directory off the GUI thread, streaming rows as it goes."""
    batch = pyqtSignal(int, list)  # (generation, rows) — partial results
    done = pyqtSignal(int, list)   # (generation, all rows, sorted)

    def __init__(self, directory: str, generation: int):
        super().__init__()
        self._directory = directory
        self._generation = generation
        self.rows: list[dict] = []   # read by the caller if wait() succeeds

    def run(self):
        self.rows = _scan_dir(
            self._directory,
            on_batch=lambda rows: self.batch.emit(self._generation, list(rows)),
        )
        self.done.emit(self._generation, self.rows)


# ── Formatters ───────────────────────────────────────────────────────────────

def _fmt_size(n: int, is_dir: bool = False) -> str:
    if is_dir:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n} TB"


def _fmt_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# A directory's entries nearly all share one uid/gid, and on a network
# directory service (LDAP/SSSD) an uncached lookup can even hit the wire.
@lru_cache(maxsize=None)
def _get_owner(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return str(uid)


@lru_cache(maxsize=None)
def _get_group(gid: int) -> str:
    try:
        import grp
        return grp.getgrgid(gid).gr_name
    except Exception:
        return str(gid)


# ── Model ────────────────────────────────────────────────────────────────────

class FileModel(QAbstractTableModel):
    """Flat directory listing as a Qt table model."""

    loading_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []
        self._cut_paths: set[str] = set()
        self._scan_generation = 0
        self._applied_generation = -1
        self._loading = False
        self._scan_workers: set = set()
        self._folder_icon = QIcon.fromTheme("folder")
        self._file_icon = QIcon.fromTheme("text-x-generic")
        if self._folder_icon.isNull():
            self._folder_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        if self._file_icon.isNull():
            self._file_icon = QIcon.fromTheme("application-octet-stream")
        if self._file_icon.isNull():
            self._file_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self._type_icon_cache: dict[str, QIcon] = {}
        self._vcs_manager = None   # set via set_vcs_manager(); kept optional so
                                    # FileModel has zero required Git dependency

    def _icon_for_file(self, path: str) -> QIcon:
        """Icon of the app set to launch this file's type (like Nautilus/Dolphin),
        falling back to a generic mime icon, then the default file icon."""
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "application/octet-stream"
        cached = self._type_icon_cache.get(mime)
        if cached is not None:
            return cached
        from src.open_with import default_app_for_mime
        icon = None
        app = default_app_for_mime(mime)
        if app and app.icon:
            themed = QIcon(app.icon) if os.path.isabs(app.icon) else QIcon.fromTheme(app.icon)
            if not themed.isNull():
                icon = themed
        if icon is None:
            generic = QIcon.fromTheme(mime.replace("/", "-"))
            if not generic.isNull():
                icon = generic
        if icon is None:
            icon = self._file_icon
        self._type_icon_cache[mime] = icon
        return icon

    def set_vcs_manager(self, manager) -> None:
        self._vcs_manager = manager

    def notify_status_changed(self) -> None:
        """Repaint Name/Git columns for every row without touching disk --
        called when VcsManager reports a repo refresh finished."""
        self._emit_visual_changed()

    def set_cut_paths(self, paths: set[str]) -> None:
        self._cut_paths = {os.path.abspath(path) for path in paths}
        self._emit_visual_changed()

    def _emit_visual_changed(self) -> None:
        if not self._rows:
            return
        top_left = self.index(0, _COL_NAME)
        bottom_right = self.index(len(self._rows) - 1, len(_ALL_COLUMNS) - 1)
        self.dataChanged.emit(
            top_left, bottom_right,
            [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.ForegroundRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.FontRole,
            ],
        )

    # A local directory scans in well under this; an MTP/SMB one does not.
    _SYNC_SCAN_DEADLINE_MS = 150

    def load(self, directory: str):
        """Populate the model from `directory`.

        The scan always runs on a worker thread, but we wait briefly for it:
        local directories finish inside the deadline and are applied inline,
        so navigation stays synchronous and the view never flickers. Slow
        mounts (MTP phones, SMB) blow the deadline and finish asynchronously
        with the list showing a loading state, rather than freezing the app
        for the seconds a scan there really takes.
        """
        self._scan_generation += 1
        generation = self._scan_generation

        worker = _ScanWorker(directory, generation)
        worker.batch.connect(self._on_scan_batch)
        worker.done.connect(self._on_scan_done)
        worker.finished.connect(lambda w=worker: self._scan_workers.discard(w))
        self._scan_workers.add(worker)   # keep a ref; a GC'd QThread crashes Qt
        worker.start()

        if worker.wait(self._SYNC_SCAN_DEADLINE_MS):
            # Finished in time — apply the whole sorted list at once. The
            # queued batch/done signals still arrive; both drop it as applied.
            self._apply_rows(generation, worker.rows)
            return

        # Still scanning: clear the stale listing and report loading, so the
        # view can't act on entries from the directory we just left. Rows now
        # arrive progressively via _on_scan_batch.
        self._loading = True
        self._apply_rows(generation, [], loading=True)
        self.loading_changed.emit(True)

    def _apply_rows(self, generation: int, rows: list, loading: bool = False):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()
        if not loading:
            self._applied_generation = generation

    def _on_scan_batch(self, generation: int, rows: list):
        if generation != self._scan_generation:
            return          # a newer navigation superseded this scan
        if generation == self._applied_generation:
            return          # the sync path already applied the full list
        start = len(self._rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def _on_scan_done(self, generation: int, rows: list):
        if generation != self._scan_generation:
            return          # a newer navigation superseded this scan
        if generation == self._applied_generation:
            return          # already applied inline by the sync path
        # Rows are already in via batches; just mark the scan finished. The
        # view sorts through the proxy, so their arrival order doesn't matter.
        self._applied_generation = generation
        self._loading = False
        self.loading_changed.emit(False)

    def is_loading(self) -> bool:
        return self._loading

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(_ALL_COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _ALL_COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == _COL_NAME:  return row["name"]
            if col == _COL_SIZE:
                if row["is_dir"]:
                    dsb = row.get("dir_size_bytes")
                    return _fmt_size(dsb) if dsb is not None else ""
                return _fmt_size(row["size"])
            if col == _COL_MTIME: return _fmt_mtime(row["mtime"])
            if col == _COL_CTIME: return _fmt_mtime(row["ctime"])
            if col == _COL_TYPE:  return "Directory" if row["is_dir"] else (row["ext"].upper() or "File")
            if col == _COL_EXT:   return row["ext"]
            if col == _COL_PERMS: return row["perms"]
            if col == _COL_OWNER: return row["owner"]
            if col == _COL_GROUP: return row["group"]
            if col == _COL_GIT:
                label, _ = _GIT_DISPLAY.get(self._git_status(row), ("", None))
                return label

        if role == Qt.ItemDataRole.EditRole and col == _COL_NAME:
            return row["name"]

        if role == Qt.ItemDataRole.UserRole:
            return row

        if role == Qt.ItemDataRole.DecorationRole and col == _COL_NAME:
            if row["is_dir"]:
                return self._folder_icon
            return self._icon_for_file(row["name"])

        if role == Qt.ItemDataRole.BackgroundRole and self._is_cut(row):
            return QBrush(QColor(72, 72, 72, 80))

        if role == Qt.ItemDataRole.ForegroundRole:
            if self._is_cut(row):
                return QBrush(QColor("#8a8a8a"))
            if col == _COL_NAME:
                _, color = _GIT_DISPLAY.get(self._git_status(row), ("", None))
                if color:
                    return QBrush(QColor(color))
            if col == _COL_GIT:
                _, color = _GIT_DISPLAY.get(self._git_status(row), ("", None))
                if color:
                    return QBrush(QColor(color))
            if row["is_dir"]:
                return QBrush(QColor("#4a90d9"))

        return None

    def _is_cut(self, row: dict) -> bool:
        return os.path.abspath(row["path"]) in self._cut_paths

    def flags(self, index):
        f = super().flags(index)
        if index.isValid():
            f |= Qt.ItemFlag.ItemIsDragEnabled
            if index.column() == _COL_NAME:
                f |= Qt.ItemFlag.ItemIsEditable
        return f

    # ── Drag out (files as URLs, accepted by other apps) ──────────

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes if i.isValid()})
        urls = [
            QUrl.fromLocalFile(self._rows[r]["path"])
            for r in rows if r < len(self._rows)
        ]
        if not urls:
            return None
        md = QMimeData()
        md.setUrls(urls)
        return md

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        """Inline rename: committing the Name editor renames the file on disk."""
        if role != Qt.ItemDataRole.EditRole or index.column() != _COL_NAME:
            return False
        if not (0 <= index.row() < len(self._rows)):
            return False
        row = self._rows[index.row()]
        new_name = str(value).strip()
        if not new_name or new_name == row["name"] or os.sep in new_name:
            return False
        new_path = os.path.join(os.path.dirname(row["path"]), new_name)
        if os.path.exists(new_path):
            return False
        try:
            os.rename(row["path"], new_path)
        except OSError:
            return False
        row["name"] = new_name
        row["path"] = new_path
        row["ext"] = "" if row["is_dir"] else os.path.splitext(new_name)[1].lstrip(".")
        first = self.index(index.row(), 0)
        last = self.index(index.row(), len(_ALL_COLUMNS) - 1)
        self.dataChanged.emit(first, last)
        return True

    def _git_status(self, row: dict) -> FileStatus:
        if self._vcs_manager is None:
            return FileStatus.CLEAN
        return self._vcs_manager.status_for(row["path"])

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self.layoutAboutToBeChanged.emit()
        rev = (order == Qt.SortOrder.DescendingOrder)
        key_fns = {
            _COL_NAME:  lambda r: (not r["is_dir"], r["name"].lower()),
            _COL_SIZE:  lambda r: (not r["is_dir"], r["dir_size_bytes"] if r["is_dir"] and r["dir_size_bytes"] is not None else r["size"]),
            _COL_MTIME: lambda r: (not r["is_dir"], r["mtime"]),
            _COL_CTIME: lambda r: (not r["is_dir"], r["ctime"]),
            _COL_TYPE:  lambda r: (not r["is_dir"], r["ext"].lower()),
            _COL_EXT:   lambda r: (not r["is_dir"], r["ext"].lower()),
            _COL_PERMS: lambda r: (not r["is_dir"], r["perms"]),
            _COL_OWNER: lambda r: (not r["is_dir"], r["owner"].lower()),
            _COL_GROUP: lambda r: (not r["is_dir"], r["group"].lower()),
            _COL_GIT:   lambda r: (not r["is_dir"], -self._git_status(r).priority),
        }
        if column in key_fns:
            self._rows.sort(key=key_fns[column], reverse=rev)
        self.layoutChanged.emit()

    def set_dir_size(self, path: str, size_bytes):
        """Update the computed size for a directory row and notify the view."""
        for i, row in enumerate(self._rows):
            if row["path"] == path:
                row["dir_size_bytes"] = int(size_bytes)
                idx = self.index(i, _COL_SIZE)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                break

    def entry(self, row_index: int) -> dict | None:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None


# ── View ─────────────────────────────────────────────────────────────────────

class _FileProxyModel(QSortFilterProxyModel):
    """Proxy that sorts Size numerically and always keeps dirs above files."""

    def lessThan(self, left, right):
        model = self.sourceModel()
        l_row = model.entry(left.row())
        r_row = model.entry(right.row())
        if l_row is None or r_row is None:
            return False

        # Dirs always sort before files regardless of column or direction
        if l_row["is_dir"] != r_row["is_dir"]:
            # Return True when left should come first (ascending).
            # The view flips the result for descending, but we want dirs fixed
            # at the top in both directions, so base it on sort order.
            asc = (self.sortOrder() == Qt.SortOrder.AscendingOrder)
            return l_row["is_dir"] if asc else not l_row["is_dir"]

        col = left.column()
        if col == _COL_SIZE:
            def _size(r):
                if r["is_dir"]:
                    return r["dir_size_bytes"] if r["dir_size_bytes"] is not None else -1
                return r["size"]
            return _size(l_row) < _size(r_row)

        return super().lessThan(left, right)


class FileListPane(QWidget):
    """Sortable, filterable file list with toggleable columns."""

    navigated       = pyqtSignal(str)   # user double-clicked a directory
    file_activated  = pyqtSignal(str)   # user double-clicked a file
    columns_changed = pyqtSignal(list)  # list[int] of visible column indices
    column_config_changed = pyqtSignal(dict)
    paths_dropped = pyqtSignal(list, str, object)  # paths, destination dir, drop action
    selection_changed = pyqtSignal()    # rows selected/deselected

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_dir = os.path.expanduser("~")
        self._restoring_columns = False

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._model = FileModel()

        self._proxy = _FileProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(_COL_NAME)

        self.table = QTableView()
        self.table.setModel(self._proxy)
        # Selection drives the status bar's "N selected / total" readout.
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self.selection_changed.emit()
        )
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setDragEnabled(True)
        self.table.setAcceptDrops(True)
        self.table.setDropIndicatorShown(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(18, 18))
        self.table.setAlternatingRowColors(True)
        # Breathing room so text doesn't touch the cell border
        self.table.setStyleSheet("QTableView::item { padding-left: 6px; padding-right: 4px; }")

        # Slow click on the name of an already-selected row starts an inline
        # rename (Explorer-style). The timer waits past the double-click
        # interval so a double-click (open) always wins.
        self._rename_timer = QTimer(self)
        self._rename_timer.setSingleShot(True)
        self._rename_timer.setInterval(QApplication.doubleClickInterval() + 150)
        self._rename_timer.timeout.connect(self._begin_rename)
        self._rename_index = QModelIndex()
        self._pressed_on_selected = False
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.table.viewport())
        self._rubber_origin = QPoint()
        self._rubber_selecting = False
        self._rubber_additive_rows: set[int] = set()

        # The DragEnter/DragMove branch below fully consumes the event (needed
        # to set the drop action from modifier keys) so it never reaches
        # QAbstractItemView.dragMoveEvent — which is what normally starts
        # Qt's built-in edge autoscroll. Drive it ourselves instead.
        self._drag_autoscroll_timer = QTimer(self)
        self._drag_autoscroll_timer.setInterval(50)
        self._drag_autoscroll_timer.timeout.connect(self._drag_autoscroll_tick)
        self._drag_autoscroll_step = 0

        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        self.table.clicked.connect(self._on_single_click)

        # All columns interactive (user can drag to resize)
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(True)
        for c in range(len(_ALL_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(c, _DEFAULT_WIDTHS[c])
        hdr.sectionMoved.connect(lambda *_args: self._emit_column_config_changed())
        hdr.sectionResized.connect(lambda *_args: self._emit_column_config_changed())
        hdr.sortIndicatorChanged.connect(lambda *_args: self._emit_column_config_changed())

        # Right-click header → column toggle menu
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._header_menu)

        # Hide non-default columns
        for c in range(len(_ALL_COLUMNS)):
            if c not in _DEFAULT_VISIBLE:
                self.table.setColumnHidden(c, True)

        v.addWidget(self.table)
        self.navigate(self.current_dir)

    # ── Git status integration ───────────────────────────────────

    def set_vcs_manager(self, manager) -> None:
        self._model.set_vcs_manager(manager)

    def notify_status_changed(self) -> None:
        self._model.notify_status_changed()

    def set_cut_paths(self, paths: set[str]) -> None:
        self._model.set_cut_paths(paths)

    # ── Column toggle ─────────────────────────────────────────────

    def _header_menu(self, pos):
        menu = QMenu(self)
        menu.setTitle("Columns")
        hdr = self.table.horizontalHeader()
        visible_cols = [
            hdr.logicalIndex(visual)
            for visual in range(hdr.count())
            if not self.table.isColumnHidden(hdr.logicalIndex(visual))
        ]
        hidden_cols = [
            col for col in range(len(_ALL_COLUMNS))
            if self.table.isColumnHidden(col)
        ]

        def add_column_action(col: int):
            name = _ALL_COLUMNS[col]
            act = QAction(name, menu)
            act.setCheckable(True)
            act.setChecked(not self.table.isColumnHidden(col))
            if col == _COL_NAME:
                act.setEnabled(False)  # Name is the anchor column and stays visible.
            else:
                act.toggled.connect(lambda checked, c=col: self._toggle_column(c, checked))
            menu.addAction(act)

        for col in visible_cols:
            add_column_action(col)
        if hidden_cols:
            menu.addSeparator()
            for col in hidden_cols:
                add_column_action(col)
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, col: int, visible: bool):
        self.table.setColumnHidden(col, not visible)
        self._emit_column_config_changed()

    def get_visible_columns(self) -> list[int]:
        return [c for c in range(len(_ALL_COLUMNS)) if not self.table.isColumnHidden(c)]

    def set_visible_columns(self, cols: list[int]):
        normalized = self._normalize_columns(cols)
        for c in range(len(_ALL_COLUMNS)):
            self.table.setColumnHidden(c, c not in normalized)

    def get_column_config(self) -> dict:
        hdr = self.table.horizontalHeader()
        sort_order = (
            "descending"
            if hdr.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
            else "ascending"
        )
        return {
            "visible_columns": self.get_visible_columns(),
            "column_order": [hdr.logicalIndex(v) for v in range(hdr.count())],
            "widths": {str(c): self.table.columnWidth(c) for c in range(len(_ALL_COLUMNS))},
            "sort_column": hdr.sortIndicatorSection(),
            "sort_order": sort_order,
        }

    def set_column_config(self, config):
        self._restoring_columns = True
        try:
            if isinstance(config, list):
                self.set_visible_columns(config)
                return
            if not isinstance(config, dict):
                return

            visible = self._normalize_columns(config.get("visible_columns", []))
            if visible:
                self.set_visible_columns(visible)

            hdr = self.table.horizontalHeader()
            order = self._normalize_columns(config.get("column_order", []))
            if len(order) == len(_ALL_COLUMNS):
                for visual, logical in enumerate(order):
                    current_visual = hdr.visualIndex(logical)
                    if current_visual != visual:
                        hdr.moveSection(current_visual, visual)

            widths = config.get("widths", {})
            if isinstance(widths, dict):
                for key, width in widths.items():
                    col = self._normalize_column_id(key)
                    if col is not None:
                        try:
                            self.table.setColumnWidth(col, int(width))
                        except (TypeError, ValueError):
                            pass

            sort_col = self._normalize_column_id(config.get("sort_column"))
            if sort_col is not None and sort_col >= 0:
                order = (
                    Qt.SortOrder.DescendingOrder
                    if config.get("sort_order") == "descending"
                    else Qt.SortOrder.AscendingOrder
                )
                self.table.sortByColumn(sort_col, order)
        finally:
            self._restoring_columns = False

    def _emit_column_config_changed(self):
        if self._restoring_columns:
            return
        self.columns_changed.emit(self.get_visible_columns())
        self.column_config_changed.emit(self.get_column_config())

    @staticmethod
    def _normalize_column_id(value) -> int | None:
        if isinstance(value, int):
            return value if 0 <= value < len(_ALL_COLUMNS) else None
        if isinstance(value, str):
            if value.isdigit():
                col = int(value)
                return col if 0 <= col < len(_ALL_COLUMNS) else None
            if value in _ALL_COLUMNS:
                return _ALL_COLUMNS.index(value)
        return None

    @classmethod
    def _normalize_columns(cls, values) -> list[int]:
        if not isinstance(values, list):
            return []
        result: list[int] = []
        for value in values:
            col = cls._normalize_column_id(value)
            if col is not None and col not in result:
                result.append(col)
        if _COL_NAME not in result:
            result.insert(0, _COL_NAME)
        return result

    # ── Slow-click rename ─────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._activate_current_or_selected():
                    event.accept()
                    return True

        if obj is self.table.viewport():
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                self._rename_timer.stop()
                idx = self.table.indexAt(event.position().toPoint())
                if not idx.isValid():
                    self._begin_rubber_band(event.position().toPoint(), event.modifiers())
                    event.accept()
                    return True
                sel = self.table.selectionModel().selectedRows()
                plain = not (event.modifiers() & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.ShiftModifier))
                self._pressed_on_selected = (
                    idx.isValid()
                    and idx.column() == _COL_NAME
                    and len(sel) == 1
                    and sel[0].row() == idx.row()
                    and plain
                )
            elif (event.type() == QEvent.Type.MouseMove
                  and self._rubber_selecting
                  and event.buttons() & Qt.MouseButton.LeftButton):
                self._update_rubber_band(event.position().toPoint())
                event.accept()
                return True
            elif (event.type() == QEvent.Type.MouseButtonRelease
                  and event.button() == Qt.MouseButton.LeftButton
                  and self._rubber_selecting):
                self._update_rubber_band(event.position().toPoint())
                self._rubber_selecting = False
                self._rubber_band.hide()
                event.accept()
                return True
            elif event.type() == QEvent.Type.MouseButtonDblClick:
                self._rename_timer.stop()
                self._pressed_on_selected = False
            elif event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData().hasUrls():
                    event.setDropAction(self._drop_action_for_modifiers(event.modifiers()))
                    event.accept()
                    self._update_drag_autoscroll(event.position().toPoint())
                    return True
            elif event.type() == QEvent.Type.DragLeave:
                self._stop_drag_autoscroll()
            elif event.type() == QEvent.Type.Drop:
                self._stop_drag_autoscroll()
                if event.mimeData().hasUrls():
                    paths = [
                        url.toLocalFile()
                        for url in event.mimeData().urls()
                        if url.isLocalFile()
                    ]
                    if paths:
                        dest_dir = self._drop_destination(event.position().toPoint())
                        action = self._drop_action_for_modifiers(event.modifiers())
                        self.paths_dropped.emit(paths, dest_dir, action)
                        event.setDropAction(action)
                        event.accept()
                        return True
        return super().eventFilter(obj, event)

    def _begin_rubber_band(self, pos: QPoint, modifiers):
        """Start Explorer-style row selection from empty table whitespace."""
        self._pressed_on_selected = False
        self._rubber_origin = pos
        self._rubber_selecting = True
        selection = self.table.selectionModel()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self._rubber_additive_rows = {idx.row() for idx in selection.selectedRows()}
        else:
            self._rubber_additive_rows = set()
            selection.clearSelection()
        self._rubber_band.setGeometry(QRect(pos, QSize()))
        self._rubber_band.show()

    def _update_rubber_band(self, pos: QPoint):
        rect = QRect(self._rubber_origin, pos).normalized()
        self._rubber_band.setGeometry(rect)

        rows = set(self._rubber_additive_rows)
        for row in range(self._proxy.rowCount()):
            cell_rect = self.table.visualRect(self._proxy.index(row, _COL_NAME))
            row_rect = QRect(0, cell_rect.top(), self.table.viewport().width(), cell_rect.height())
            if cell_rect.isValid() and rect.intersects(row_rect):
                rows.add(row)

        selection = self.table.selectionModel()
        selection.clearSelection()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for row in sorted(rows):
            selection.select(self._proxy.index(row, _COL_NAME), flags)

    _DRAG_AUTOSCROLL_MARGIN = 24   # px from the top/bottom edge that triggers scrolling
    _DRAG_AUTOSCROLL_STEP = 24     # px nudged per timer tick, close to a wheel notch

    def _update_drag_autoscroll(self, pos: QPoint):
        height = self.table.viewport().height()
        if pos.y() < self._DRAG_AUTOSCROLL_MARGIN:
            self._drag_autoscroll_step = -self._DRAG_AUTOSCROLL_STEP
        elif pos.y() > height - self._DRAG_AUTOSCROLL_MARGIN:
            self._drag_autoscroll_step = self._DRAG_AUTOSCROLL_STEP
        else:
            self._drag_autoscroll_step = 0
        if self._drag_autoscroll_step and not self._drag_autoscroll_timer.isActive():
            self._drag_autoscroll_timer.start()
        elif not self._drag_autoscroll_step:
            self._drag_autoscroll_timer.stop()

    def _drag_autoscroll_tick(self):
        bar = self.table.verticalScrollBar()
        bar.setValue(bar.value() + self._drag_autoscroll_step)

    def _stop_drag_autoscroll(self):
        self._drag_autoscroll_timer.stop()
        self._drag_autoscroll_step = 0

    def _drop_destination(self, pos) -> str:
        idx = self.table.indexAt(pos)
        if idx.isValid():
            src = self._proxy.mapToSource(idx)
            entry = self._model.entry(src.row())
            if entry and entry["is_dir"]:
                return entry["path"]
        return self.current_dir

    @staticmethod
    def _drop_action_for_modifiers(modifiers) -> Qt.DropAction:
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return Qt.DropAction.MoveAction
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return Qt.DropAction.CopyAction
        return Qt.DropAction.CopyAction

    def _on_single_click(self, proxy_idx):
        if self._pressed_on_selected and proxy_idx.column() == _COL_NAME:
            self._rename_index = proxy_idx
            self._rename_timer.start()
        self._pressed_on_selected = False

    def _begin_rename(self):
        idx = self._rename_index
        if idx.isValid() and self.table.currentIndex().row() == idx.row():
            self.table.edit(idx)

    def rename_current(self):
        """Start inline rename of the current row (e.g. bound to F2)."""
        idx = self.table.currentIndex()
        if idx.isValid():
            self.table.edit(idx.siblingAtColumn(_COL_NAME))

    # ── Navigation & events ───────────────────────────────────────

    def navigate(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path):
            self.current_dir = path
            self._model.load(path)

    def set_filter(self, text: str):
        self._proxy.setFilterWildcard(f"*{text}*" if text else "")

    def _on_double_click(self, proxy_idx):
        self._activate_proxy_index(proxy_idx)

    def _activate_current_or_selected(self) -> bool:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            idx = self.table.currentIndex()
            if not idx.isValid():
                return False
            rows = [idx]

        entries = []
        for row in rows:
            src = self._proxy.mapToSource(row)
            entry = self._model.entry(src.row())
            if entry:
                entries.append(entry)
        if not entries:
            return False

        dirs = [e for e in entries if e["is_dir"]]
        if dirs:
            self.navigate(dirs[0]["path"])
            self.navigated.emit(self.current_dir)
        else:
            for entry in entries:
                self.file_activated.emit(entry["path"])
        return True

    def _activate_proxy_index(self, proxy_idx):
        src = self._proxy.mapToSource(proxy_idx)
        entry = self._model.entry(src.row())
        if entry:
            if entry["is_dir"]:
                self.navigate(entry["path"])
                self.navigated.emit(self.current_dir)
            else:
                self.file_activated.emit(entry["path"])

    def selected_paths(self) -> list[str]:
        rows = {self._proxy.mapToSource(i).row() for i in self.table.selectedIndexes()}
        return [
            self._model.entry(r)["path"]
            for r in sorted(rows)
            if self._model.entry(r) is not None
        ]

    def selection_size(self) -> tuple[int, bool]:
        """(total bytes selected, whether any directory size is unknown).

        Read from rows already in the model, never by stat'ing: this runs on
        every selection change, and touching the filesystem here would stall
        the UI on an MTP or network mount.

        A directory contributes only if "Get Size" has measured it, so the
        caller is told when the total is partial rather than being handed a
        number that silently understates the selection.
        """
        rows = {self._proxy.mapToSource(i).row() for i in self.table.selectedIndexes()}
        total = 0
        partial = False
        for r in rows:
            entry = self._model.entry(r)
            if entry is None:
                continue
            if entry["is_dir"]:
                measured = entry.get("dir_size_bytes")
                if measured is None:
                    partial = True
                else:
                    total += measured
            else:
                total += entry["size"]
        return total, partial

    def set_dir_size(self, path: str, size_bytes: int):
        self._model.set_dir_size(path, size_bytes)

    def apply_font(self, font):
        """Explicitly push a font to the table and header (bypasses stylesheet blocking)."""
        from PyQt6.QtGui import QFontMetrics
        self.table.setFont(font)
        self.table.horizontalHeader().setFont(font)
        row_h = QFontMetrics(font).height() + 8
        self.table.verticalHeader().setDefaultSectionSize(row_h)

    def item_count(self) -> int:
        return self._model.rowCount()

    def refresh(self, preserve: bool = True):
        """Reload the current directory.

        An automatic refresh fires while the user is working, so by default
        the selection and scroll position are put back afterwards; without
        that, a background copy landing in the folder would clear a
        selection mid-task.
        """
        if not preserve:
            self._model.load(self.current_dir)
            return

        selected = set(self.selected_paths())
        scroll = self.table.verticalScrollBar().value()
        current = self.table.currentIndex()
        current_path = None
        if current.isValid():
            entry = self._model.entry(self._proxy.mapToSource(current).row())
            current_path = entry["path"] if entry else None

        self._model.load(self.current_dir)
        if self._model.is_loading():
            # Rows arrive later on a slow mount; restore once they land.
            def when_done(loading: bool):
                if not loading:
                    self._model.loading_changed.disconnect(when_done)
                    self._reselect(selected, scroll, current_path)
            self._model.loading_changed.connect(when_done)
        else:
            self._reselect(selected, scroll, current_path)

    def _reselect(self, paths: set[str], scroll: int, current_path: str | None):
        """Re-apply a selection by path after the rows were replaced."""
        if not paths and current_path is None:
            return
        sel_model = self.table.selectionModel()
        selection = QItemSelection()
        current_index = None
        for row in range(self._model.rowCount()):
            entry = self._model.entry(row)
            if entry is None or entry["path"] not in paths and entry["path"] != current_path:
                continue
            idx = self._proxy.mapFromSource(self._model.index(row, 0))
            if not idx.isValid():
                continue
            if entry["path"] in paths:
                selection.select(idx, idx.siblingAtColumn(len(_ALL_COLUMNS) - 1))
            if entry["path"] == current_path:
                current_index = idx
        if not selection.isEmpty():
            sel_model.select(
                selection,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        if current_index is not None:
            sel_model.setCurrentIndex(
                current_index, QItemSelectionModel.SelectionFlag.NoUpdate
            )
        self.table.verticalScrollBar().setValue(scroll)
