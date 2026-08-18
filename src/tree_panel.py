"""Left directory tree — lazy-loaded QTreeWidget, plus a segregated Devices/
Network section that never scrolls with it (see TreePanel at the bottom)."""

import json
import os
import re
import subprocess
import signal
import time
from urllib.parse import quote, unquote, urlparse

from PyQt6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QMenu, QInputDialog, QLineEdit,
    QDialog, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox,
    QWidget, QSplitter,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QAction, QBrush, QColor, QIcon

from src.vcs.status import FileStatus
from src.credential_store import CredentialStore

_DUMMY = "__dummy__"
_ROLE_ACTIVATION_ROOT = Qt.ItemDataRole.UserRole + 1
_ROLE_SMB_KIND = Qt.ItemDataRole.UserRole + 2
_ROLE_SMB_SERVER = Qt.ItemDataRole.UserRole + 3
_ROLE_SMB_SHARE = Qt.ItemDataRole.UserRole + 4
_MAX_DIRS = 300  # cap per level to avoid massive trees
# MTP backends that the desktop respawns on demand, so evicting one to free a
# stuck device costs the user nothing. gvfsd-mtp belongs here because a failed
# `gio mount` leaves one behind still holding the node, which blocks the retry.
# Any holder outside this set is a real application: report it, never kill it.
_MTP_BACKENDS = frozenset({
    "kiod5", "kiod6", "kioslave5", "kioslave6", "kio_mtp", "gvfsd-mtp",
})
_MOUNT_TIMEOUT = "__mount_timeout__"
_MOUNT_NO_GIO = "__mount_no_gio__"


def _is_dir_quiet(entry: os.DirEntry) -> bool:
    """entry.is_dir() treating an unreadable entry as 'not a directory'."""
    try:
        return entry.is_dir()
    except OSError:
        return False

# Only "is this subtree dirty" matters at tree granularity -- a single color
# keeps folders scannable instead of running the full file palette here.
_DIRTY_COLOR = QColor("#e5a642")
_REPO_ROOT_COLOR = QColor("#4a9eff")
_DEVICE_COLOR = QColor("#6bb86b")


class _SmbCredentialsDialog(QDialog):
    def __init__(self, server: str, share: str, can_save: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect to SMB share")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Connect to {server}/{share}"))
        layout.addWidget(QLabel("Username:"))
        self.username = QLineEdit(os.environ.get("USER", ""))
        layout.addWidget(self.username)
        layout.addWidget(QLabel("Windows domain / workgroup:"))
        self.domain = QLineEdit("WORKGROUP")
        layout.addWidget(self.domain)
        layout.addWidget(QLabel("Password:"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.password)
        self.save_login = QCheckBox("Save login/password in KDE Wallet")
        self.save_login.setEnabled(can_save)
        if not can_save:
            self.save_login.setToolTip("kwallet-query is not installed")
        layout.addWidget(self.save_login)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def credentials(self) -> dict:
        return {
            "username": self.username.text(),
            "domain": self.domain.text(),
            "password": self.password.text(),
            "save": self.save_login.isChecked(),
        }


class _SmbScanWorker(QThread):
    """Discover SMB servers and anonymously visible shares off the UI thread."""

    scanned = pyqtSignal(list, str)

    @staticmethod
    def _run(args: list[str], timeout: int = 6) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

    def run(self):
        servers: dict[str, dict] = {}
        errors: list[str] = []

        # Modern SMB discovery advertised through mDNS/Avahi.
        try:
            result = self._run(["avahi-browse", "-rtp", "_smb._tcp"], 8)
            for line in result.stdout.splitlines():
                fields = line.split(";")
                if len(fields) >= 9 and fields[0] == "=" and fields[3]:
                    name, hostname, address = fields[3], fields[6], fields[7]
                    host = hostname.rstrip(".") or address
                    key = name.casefold()
                    existing = servers.get(key)
                    # Avahi commonly reports the same service over IPv6 and
                    # IPv4. Prefer IPv4 and show one server node.
                    if existing is None or (":" in existing.get("address", "") and ":" not in address):
                        servers[key] = {"name": name, "host": host, "address": address, "shares": []}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            errors.append("Avahi discovery unavailable")

        # NetBIOS discovery catches older Windows/Samba machines without mDNS.
        try:
            result = self._run(["nmblookup", "*"], 8)
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) < 2 or fields[1] != "*<00>":
                    continue
                address = fields[0]
                if any(server.get("address") == address for server in servers.values()):
                    continue
                name = address
                try:
                    names = self._run(["nmblookup", "-A", address], 4).stdout.splitlines()
                    for entry in names:
                        if "<00>" in entry and "<GROUP>" not in entry:
                            candidate = entry.strip().split()[0]
                            if candidate:
                                name = candidate
                                break
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
                key = name.casefold()
                servers.setdefault(key, {"name": name, "host": address, "address": address, "shares": []})
        except (FileNotFoundError, subprocess.TimeoutExpired):
            errors.append("NetBIOS discovery unavailable")

        # Enumerate shares that permit anonymous discovery. Authenticated or
        # hidden shares can still be entered using "Connect to SMB Share…".
        for server in servers.values():
            try:
                result = self._run(["smbclient", "-L", f"//{server['host']}", "-N", "-g"], 6)
                shares = []
                for line in result.stdout.splitlines():
                    fields = line.split("|", 2)
                    if len(fields) >= 2 and fields[0] == "Disk" and not fields[1].endswith("$"):
                        shares.append(fields[1])
                server["shares"] = sorted(set(shares), key=str.casefold)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                errors.append(f"Could not enumerate {server['name']}")

        ordered = sorted(servers.values(), key=lambda server: server["name"].casefold())
        self.scanned.emit(ordered, "; ".join(dict.fromkeys(errors)))


class _LazyTree(QTreeWidget):
    """Shared plumbing for the two trees TreePanel is built from: per-item
    git-status coloring, and lazy directory materialization so expanding a
    node (or refreshing an item from its context menu) fills real children
    on demand instead of scanning the whole subtree up front.
    """

    def __init__(self, panel: "TreePanel", parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setHeaderHidden(True)
        self.setAnimated(True)
        self.setIndentation(16)
        self.setColumnCount(1)
        self._vcs = None
        self._items_by_path: dict[str, QTreeWidgetItem] = {}
        self.itemExpanded.connect(self._on_expand)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    # ── Git status integration ──────────────────────────────────────

    def set_vcs_manager(self, manager) -> None:
        self._vcs = manager
        self.notify_status_changed()

    def notify_status_changed(self) -> None:
        """Reapply colors to currently-materialized items from the cache --
        no filesystem or git access, so this is safe to call on every
        repo_status_changed signal even with huge trees.
        """
        for path, item in self._items_by_path.items():
            self._apply_status(item, path)

    def _apply_status(self, item: QTreeWidgetItem, path: str) -> None:
        if self._vcs is None:
            item.setForeground(0, QBrush())
            font = item.font(0)
            font.setBold(False)
            item.setFont(0, font)
            return
        is_root = self._vcs.is_repo_root(path)
        status = self._vcs.status_for_dir(path)
        font = item.font(0)
        font.setBold(is_root)
        item.setFont(0, font)
        if is_root:
            item.setForeground(0, QBrush(_REPO_ROOT_COLOR))
        elif status != FileStatus.CLEAN:
            item.setForeground(0, QBrush(_DIRTY_COLOR))
        else:
            item.setForeground(0, QBrush())

    def _make_item(self, label: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        self._items_by_path[path] = item
        self._apply_status(item, path)
        return item

    def _forget_subtree(self, item: QTreeWidgetItem):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self._items_by_path.pop(path, None)
        for i in range(item.childCount()):
            self._forget_subtree(item.child(i))

    def _fill_children(self, parent: QTreeWidgetItem, path: str):
        """Replace children of *parent* with real subdirectories."""
        for i in range(parent.childCount()):
            self._forget_subtree(parent.child(i))
        parent.takeChildren()
        # scandir resolves is_dir from the directory entry itself where the
        # filesystem provides it. listdir + isdir per name costs an extra
        # round trip each, which on MTP/SMB means seconds per folder.
        try:
            with os.scandir(path) as it:
                dirs = sorted(
                    entry.name for entry in it
                    if not entry.name.startswith(".") and _is_dir_quiet(entry)
                )
        except (OSError, PermissionError):
            return
        for name in dirs[:_MAX_DIRS]:
            child = self._make_item(name, os.path.join(path, name))
            child.addChild(QTreeWidgetItem([_DUMMY]))  # placeholder → expandable
            parent.addChild(child)

    def _on_expand(self, item: QTreeWidgetItem):
        # Fill only the expanded node. Children already carry a placeholder so
        # they render as expandable; eagerly filling them a level deeper meant
        # scanning every sibling directory, which costs seconds per folder on
        # MTP/SMB and bought nothing visible.
        for i in range(item.childCount()):
            child = item.child(i)
            if child.text(0) == _DUMMY:
                path = item.data(0, Qt.ItemDataRole.UserRole)
                self._fill_children(item, path)
                return

    def refresh(self, path: str | None = None):
        """Refresh one materialized directory node, preserving selection."""
        if path is None:
            current = self.currentItem()
            path = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        if not path:
            return
        path = os.path.abspath(path)
        item = self._items_by_path.get(path)
        if item is None or not os.path.isdir(path):
            return
        was_expanded = item.isExpanded()
        self._fill_children(item, path)
        item.setExpanded(was_expanded)
        self.setCurrentItem(item)
        self.scrollToItem(item)

    def _generic_item_context_menu(self, item: QTreeWidgetItem, pos):
        """Open / Bookmark / Terminal / Refresh — for any item that carries a
        real filesystem path (a plain directory, or a mounted device root).
        """
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        name = os.path.basename(path) or path

        menu = QMenu(self)
        menu.addAction(QAction(f'Open "{name}"', menu,
                               triggered=lambda: self.panel.dir_selected.emit(path)))
        menu.addSeparator()
        menu.addAction(QAction("Add to Bookmarks", menu,
                               triggered=lambda: self.panel.bookmark_requested.emit(path)))
        menu.addAction(QAction("Open in Terminal", menu,
                               triggered=lambda: self.panel.open_terminal_requested.emit(path)))
        menu.addSeparator()
        menu.addAction(QAction("Refresh", menu,
                               triggered=lambda: self.refresh(path)))
        menu.exec(self.mapToGlobal(pos))


class _DirectoryTree(_LazyTree):
    """The scrolling filesystem tree — everything under "/" (or drive roots
    on Windows). No Devices/Network content lives here.
    """

    def __init__(self, panel: "TreePanel", parent=None):
        super().__init__(panel, parent)
        self._populate_root()
        self.itemClicked.connect(self._on_click)
        self.customContextMenuRequested.connect(self._tree_context_menu)

    def _populate_root(self):
        roots = ["/"] if os.name != "nt" else TreePanel._win_drives()
        for root in roots:
            item = self._make_item(os.path.basename(root) or root, root)
            self._fill_children(item, root)
            self.addTopLevelItem(item)

    def _on_click(self, item: QTreeWidgetItem, _column: int):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.panel.dir_selected.emit(path)

    def _tree_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        self._generic_item_context_menu(item, pos)

    def navigate(self, path: str):
        """Expand and select the tree node for *path*."""
        path = os.path.abspath(path)

        # Build ancestry from root down to path
        parts: list[str] = []
        p = path
        while True:
            parts.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        parts.reverse()  # ["/", "/home", "/home/user", ...]

        # Find matching top-level item
        root_part = parts[0]
        current: QTreeWidgetItem | None = None
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == root_part:
                current = item
                break
        if current is None:
            return

        for part in parts[1:]:
            # Ensure children are loaded
            if current.childCount() == 1 and current.child(0).text(0) == _DUMMY:
                cur_path = current.data(0, Qt.ItemDataRole.UserRole)
                self._fill_children(current, cur_path)

            found: QTreeWidgetItem | None = None
            for i in range(current.childCount()):
                child = current.child(i)
                cp = child.data(0, Qt.ItemDataRole.UserRole)
                if cp and os.path.normpath(cp) == os.path.normpath(part):
                    found = child
                    break
            if found is None:
                # The tree is only a lazy snapshot: directories created after
                # population, hidden directories, and entries beyond the
                # per-level display cap may be absent even though the address
                # is valid. Materialize the requested ancestry on demand so
                # tree visibility never controls whether navigation succeeds.
                current_path = current.data(0, Qt.ItemDataRole.UserRole)
                if (not current_path
                        or os.path.normpath(os.path.dirname(part)) != os.path.normpath(current_path)
                        or not os.path.isdir(part)):
                    return
                found = self._make_item(os.path.basename(part) or part, part)
                found.addChild(QTreeWidgetItem([_DUMMY]))
                current.addChild(found)
            current.setExpanded(True)
            current = found

        if current is not None:
            self.setCurrentItem(current)
            self.scrollToItem(current)


class _DevicesTree(_LazyTree):
    """Devices + Network Neighborhood — its own fixed-section tree so it
    never scrolls out of view with the (often much longer) directory tree.
    """

    def __init__(self, panel: "TreePanel", parent=None):
        super().__init__(panel, parent)
        self._device_items: list[QTreeWidgetItem] = []
        self._devices_root: QTreeWidgetItem | None = None
        self._no_devices_item: QTreeWidgetItem | None = None
        self._network_root: QTreeWidgetItem | None = None
        self._smb_worker: _SmbScanWorker | None = None
        self._credentials = CredentialStore()

        self._populate_devices()
        self.itemClicked.connect(self._on_click)
        self.customContextMenuRequested.connect(self._tree_context_menu)

        # Poll for device changes every 5 seconds
        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._refresh_devices)
        self._device_timer.start(5000)

    # ── Build / populate ────────────────────────────────────────────

    def _populate_devices(self):
        root = QTreeWidgetItem(["Devices"])
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)
        root.setForeground(0, QBrush(_DEVICE_COLOR))
        refresh_icon = QIcon.fromTheme("view-refresh")
        if refresh_icon.isNull():
            root.setText(0, "⟳  Devices")
        else:
            root.setIcon(0, refresh_icon)
        root.setToolTip(0, "Click to rescan attached devices")
        self.addTopLevelItem(root)
        self._devices_root = root
        self._fill_devices()
        self._populate_network()

    def _fill_devices(self):
        """Rebuild the children of the Devices node from the current device list."""
        root = self._devices_root
        if root is None:
            return
        for child in self._device_items:
            path = child.data(0, Qt.ItemDataRole.UserRole)
            if path:
                self._items_by_path.pop(path, None)
            root.removeChild(child)
        self._device_items = []
        if self._no_devices_item is not None:
            root.removeChild(self._no_devices_item)
            self._no_devices_item = None

        devices = TreePanel._detect_devices()
        for dev in devices:
            size = f" ({dev['size']})" if dev.get("size") else ""
            activation_root = dev.get("activation_root")
            if dev["mountpoint"] is None and activation_root:
                label = f"{dev['label']} (tap to connect)"
                item = QTreeWidgetItem([label])
                item.setData(0, _ROLE_ACTIVATION_ROOT, activation_root)
            else:
                label = f"{dev['label']}{size}"
                item = self._make_item(label, dev["mountpoint"])
            root.addChild(item)
            self._device_items.append(item)
        if not devices:
            placeholder = QTreeWidgetItem(["(no devices attached)"])
            placeholder.setDisabled(True)
            root.addChild(placeholder)
            self._no_devices_item = placeholder

    def _populate_network(self):
        root = self._devices_root
        if root is None:
            return
        network = QTreeWidgetItem(["Network Neighborhood (SMB)"])
        network.setData(0, _ROLE_SMB_KIND, "network")
        network.setForeground(0, QBrush(_DEVICE_COLOR))
        refresh_icon = QIcon.fromTheme("view-refresh")
        if refresh_icon.isNull():
            network.setText(0, "⟳  Network Neighborhood (SMB)")
        else:
            network.setIcon(0, refresh_icon)
        network.setToolTip(0, "Scan the network for Windows and Samba shares")
        # Network browsing is a peer of local/removable Devices, not a device
        # contained by that category.
        self.addTopLevelItem(network)
        self._network_root = network
        prompt = QTreeWidgetItem(["Click the refresh icon to scan the network"])
        prompt.setDisabled(True)
        network.addChild(prompt)

    def _scan_smb_network(self):
        network = self._network_root
        if network is None or (self._smb_worker is not None and self._smb_worker.isRunning()):
            return
        network.takeChildren()
        scanning = QTreeWidgetItem(["Scanning network…"])
        scanning.setDisabled(True)
        network.addChild(scanning)
        network.setExpanded(True)

        worker = _SmbScanWorker(self)
        worker.scanned.connect(self._on_smb_scanned)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(self, "_smb_worker", None))
        self._smb_worker = worker
        worker.start()

    def _on_smb_scanned(self, servers: list[dict], error: str):
        network = self._network_root
        if network is None:
            return
        network.takeChildren()
        for server in servers:
            label = server["name"]
            if server.get("address") and server["address"].casefold() != label.casefold():
                label += f"  ({server['address']})"
            server_item = QTreeWidgetItem([label])
            server_item.setData(0, _ROLE_SMB_KIND, "server")
            server_item.setData(0, _ROLE_SMB_SERVER, server["host"])
            server_item.setIcon(0, QIcon.fromTheme("network-server"))
            network.addChild(server_item)
            for share in server.get("shares", []):
                share_item = QTreeWidgetItem([share])
                share_item.setData(0, _ROLE_SMB_KIND, "share")
                share_item.setData(0, _ROLE_SMB_SERVER, server["host"])
                share_item.setData(0, _ROLE_SMB_SHARE, share)
                share_item.setIcon(0, QIcon.fromTheme("folder-remote"))
                server_item.addChild(share_item)
            if not server.get("shares"):
                child = QTreeWidgetItem(["(no anonymously visible shares)"])
                child.setDisabled(True)
                server_item.addChild(child)
        if not servers:
            message = "No SMB servers found"
            if error:
                message += f" — {error}"
            empty = QTreeWidgetItem([message])
            empty.setDisabled(True)
            network.addChild(empty)
        network.setExpanded(True)

    def _refresh_devices(self):
        was_expanded = self._devices_root.isExpanded() if self._devices_root else False
        self._fill_devices()
        if self._devices_root is not None:
            self._devices_root.setExpanded(was_expanded)

    def reveal_devices(self):
        """Expand and scroll to the Devices node (jump-to-devices toolbar button)."""
        if self._devices_root is None:
            return
        self._refresh_devices()
        self._devices_root.setExpanded(True)
        self.scrollToItem(self._devices_root)
        self.setCurrentItem(self._devices_root)

    def reveal_network(self):
        """Expand and scroll to the Network Neighborhood node (jump-to-network toolbar button)."""
        if self._network_root is None:
            return
        self._network_root.setExpanded(True)
        self.scrollToItem(self._network_root)
        self.setCurrentItem(self._network_root)

    # ── Events ────────────────────────────────────────────────────

    def _on_click(self, item: QTreeWidgetItem, _column: int):
        if item is self._devices_root:
            self._refresh_devices()
            item.setExpanded(True)
            return
        smb_kind = item.data(0, _ROLE_SMB_KIND)
        if smb_kind == "network":
            self._scan_smb_network()
            return
        if smb_kind == "server":
            item.setExpanded(not item.isExpanded())
            return
        if smb_kind == "share":
            self._connect_smb_share(
                item.data(0, _ROLE_SMB_SERVER),
                item.data(0, _ROLE_SMB_SHARE),
            )
            return
        activation_root = item.data(0, _ROLE_ACTIVATION_ROOT)
        if activation_root:
            self._mount_and_navigate(activation_root)
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.panel.dir_selected.emit(path)

    def _mount_and_navigate(self, activation_root: str):
        """Mount an MTP volume on demand, then refresh and jump to it."""
        host = activation_root.rstrip("/").rsplit("/", 1)[-1]
        detail = TreePanel._gio_mount_mtp(activation_root)
        if detail is not None:
            # "Unable to open MTP device" nearly always means another client already
            # holds the USB node, not that the phone is locked: MTP allows exactly one.
            # KDE's KIO workers grab it whenever Dolphin so much as previews the phone,
            # and they never let go. They restart on demand, so evicting one is safe.
            node = TreePanel._mtp_device_node(detail)
            holders = TreePanel._usb_device_holders(node) if node else []
            if holders and all(comm in _MTP_BACKENDS for _, comm in holders):
                TreePanel._release_usb_device(node, holders)
                detail = TreePanel._gio_mount_mtp(activation_root)
            if detail is not None:
                self.panel.device_connection_failed.emit(TreePanel._mount_failure_message(detail, holders))
                return
        self._refresh_devices()
        if self._devices_root is not None:
            self._devices_root.setExpanded(True)
        for dev in TreePanel._detect_mtp_devices():
            root_host = (dev.get("activation_root") or "").rstrip("/").rsplit("/", 1)[-1]
            if dev["mountpoint"] and (root_host == host or host in dev["mountpoint"]):
                self.panel.dir_selected.emit(dev["mountpoint"])
                return
        self.panel.device_connection_failed.emit(
            "The phone mounted, but its storage location was not exposed by GVFS. "
            "Disconnect and reconnect the phone, then select File Transfer / MTP."
        )

    def connect_smb_uri(self, uri: str):
        parsed = urlparse(uri)
        if parsed.scheme.casefold() != "smb" or not parsed.hostname:
            self.panel.device_connection_failed.emit(f"Invalid SMB bookmark: {uri}")
            return
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parts:
            self.panel.device_connection_failed.emit("An SMB bookmark must include a share name.")
            return
        self._connect_smb_share(parsed.hostname, parts[0], os.path.join(*parts[1:]) if len(parts) > 1 else "")

    def _connect_smb_share(self, server: str, share: str, subpath: str = ""):
        if not server or not share:
            return
        mountpoint = TreePanel._smb_mountpoint(server, share)
        if mountpoint:
            destination = os.path.join(mountpoint, subpath) if subpath else mountpoint
            self.panel.dir_selected.emit(destination)
            return

        saved = self._credentials.get(server, share)
        save_credentials = False
        if saved is not None:
            username = saved["username"]
            domain = saved["domain"]
            password = saved["password"]
        else:
            dialog = _SmbCredentialsDialog(
                server, share, self._credentials.available(), parent=self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            entered = dialog.credentials()
            username = entered["username"]
            domain = entered["domain"]
            password = entered["password"]
            save_credentials = entered["save"]

        uri = f"smb://{quote(server, safe='.-:[]')}/{quote(share, safe='')}"
        try:
            result = subprocess.run(
                ["gio", "mount", uri], input=f"{username}\n{domain}\n{password}\n",
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            self.panel.device_connection_failed.emit(f"Timed out connecting to {server}/{share}.")
            return
        except FileNotFoundError:
            self.panel.device_connection_failed.emit("GIO is not installed, so SMB shares cannot be mounted.")
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            self.panel.device_connection_failed.emit(
                f"Could not connect to SMB share {server}/{share}."
                + (f"\n\nGIO: {detail}" if detail else "")
            )
            return
        if save_credentials and not self._credentials.save(server, share, username, domain, password):
            self.panel.device_connection_failed.emit(
                "The share connected, but Traverse could not save its credentials in KDE Wallet."
            )
        self._open_smb_when_ready(server, share, subpath=subpath)

    def _open_smb_when_ready(
        self, server: str, share: str, attempts_left: int = 20, subpath: str = "",
    ):
        """Wait for gvfsd-fuse to publish a mount after `gio mount` exits."""
        mountpoint = TreePanel._smb_mountpoint(server, share)
        if mountpoint:
            destination = os.path.join(mountpoint, subpath) if subpath else mountpoint
            self.panel.dir_selected.emit(destination)
            return
        if attempts_left > 0:
            QTimer.singleShot(
                150,
                lambda: self._open_smb_when_ready(server, share, attempts_left - 1, subpath),
            )
            return
        self.panel.device_connection_failed.emit(
            f"{server}/{share} mounted, but GVFS did not expose its filesystem path."
        )

    def _connect_smb_manual(self):
        location, ok = QInputDialog.getText(
            self, "Connect to SMB share", "Server and share (server/share or smb://server/share):",
        )
        if not ok or not location.strip():
            return
        location = location.strip()
        if location.casefold().startswith("smb://"):
            location = location[6:]
        parts = location.strip("/").split("/", 1)
        if len(parts) != 2 or not all(parts):
            self.panel.device_connection_failed.emit("Enter an SMB location as server/share.")
            return
        self._connect_smb_share(parts[0], parts[1])

    def _connect_smb_manual_for_server(self, server: str):
        share, ok = QInputDialog.getText(self, "Connect to SMB share", f"Share name on {server}:")
        if ok and share.strip():
            self._connect_smb_share(server, share.strip().strip("/"))

    def _tree_context_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        if item is self._devices_root:
            menu = QMenu(self)
            menu.addAction(QAction("Refresh", menu,
                                   triggered=self._refresh_devices))
            menu.exec(self.mapToGlobal(pos))
            return
        if item is self._network_root:
            menu = QMenu(self)
            refresh = QAction(QIcon.fromTheme("view-refresh"), "Scan network", menu)
            refresh.triggered.connect(self._scan_smb_network)
            menu.addAction(refresh)
            menu.addAction(QAction("Connect to SMB Share…", menu,
                                   triggered=self._connect_smb_manual))
            menu.exec(self.mapToGlobal(pos))
            return
        smb_kind = item.data(0, _ROLE_SMB_KIND)
        if smb_kind == "server":
            menu = QMenu(self)
            server = item.data(0, _ROLE_SMB_SERVER)
            menu.addAction(QAction(
                "Connect to share…", menu,
                triggered=lambda: self._connect_smb_manual_for_server(server),
            ))
            menu.exec(self.mapToGlobal(pos))
            return
        if smb_kind == "share":
            menu = QMenu(self)
            server = item.data(0, _ROLE_SMB_SERVER)
            share = item.data(0, _ROLE_SMB_SHARE)
            menu.addAction(QAction(
                f'Connect to "{share}"', menu,
                triggered=lambda: self._connect_smb_share(server, share),
            ))
            uri = f"smb://{quote(server, safe='.-:[]')}/{quote(share, safe='')}"
            menu.addAction(QAction(
                "Add to Bookmarks", menu,
                triggered=lambda: self.panel.bookmark_requested.emit(uri),
            ))
            menu.addSeparator()
            menu.addAction(QAction(
                "Forget saved login/password", menu,
                triggered=lambda: self._credentials.delete(server, share),
            ))
            menu.exec(self.mapToGlobal(pos))
            return
        self._generic_item_context_menu(item, pos)


class TreePanel(QWidget):
    """Directory tree on top, Devices/Network Neighborhood in their own
    fixed section below — a hard split via QSplitter, so the bottom section
    never scrolls with the (often much longer) directory tree above it.
    Drag the handle between them to resize either side.
    """

    dir_selected              = pyqtSignal(str)
    bookmark_requested        = pyqtSignal(str)
    open_terminal_requested   = pyqtSignal(str)
    device_connection_failed  = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet(
            "QSplitter::handle{background:#3a3a3a;"
            "border-top:1px solid #5a5a5a;border-bottom:1px solid #1a1a1a;}"
            "QSplitter::handle:hover{background:#5a8fd6;}"
        )

        self._dir_tree = _DirectoryTree(self)
        self._dev_tree = _DevicesTree(self)

        splitter.addWidget(self._dir_tree)
        splitter.addWidget(self._dev_tree)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([600, 170])

        layout.addWidget(splitter)
        self._splitter = splitter

    # ── Git status integration (applies to both the directory tree and
    #    mounted device roots) ────────────────────────────────────────

    def set_vcs_manager(self, manager) -> None:
        self._dir_tree.set_vcs_manager(manager)
        self._dev_tree.set_vcs_manager(manager)

    def notify_status_changed(self) -> None:
        self._dir_tree.notify_status_changed()
        self._dev_tree.notify_status_changed()

    # ── Directory tree forwarding ───────────────────────────────────

    def navigate(self, path: str):
        self._dir_tree.navigate(path)

    def refresh(self, path: str | None = None):
        self._dir_tree.refresh(path)

    def currentItem(self):
        return self._dir_tree.currentItem()

    @property
    def _items_by_path(self):
        return self._dir_tree._items_by_path

    # ── Devices / Network forwarding ────────────────────────────────

    def reveal_devices(self):
        self._dev_tree.reveal_devices()

    def reveal_network(self):
        self._dev_tree.reveal_network()

    def connect_smb_uri(self, uri: str):
        self._dev_tree.connect_smb_uri(uri)

    # ── Pure helpers (device/mount/SMB logic with no widget state) ──

    @staticmethod
    def _win_drives():
        import string
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]

    @staticmethod
    def _detect_devices() -> list[dict]:
        """Return list of removable/mountable devices with mount points.

        Uses lsblk --json to find non-system devices that are mounted.
        Filters out system disks (nvme, root, boot) and shows only
        removable or externally-attached storage.
        """
        try:
            result = subprocess.run(
                ["lsblk", "--json", "-o", "NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL,MODEL,TRAN"],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
                json.JSONDecodeError, FileNotFoundError):
            return []

        devices: list[dict] = []
        seen_mounts: set[str] = set()

        def walk(blocks, parent_tran=None):
            for block in blocks:
                tran = block.get("tran") or parent_tran
                mp = block.get("mountpoint")
                if mp and mp not in ("/", "/boot", "/boot/efi", "/home") and mp not in seen_mounts:
                    # Only show removable or non-system devices
                    is_removable = tran in ("usb", "mmc", "sdio", "ata")
                    is_media_mount = mp.startswith("/media/") or mp.startswith("/run/media/")
                    if is_removable or is_media_mount:
                        label = block.get("label") or block.get("name", "")
                        devices.append({
                            "label": label,
                            "mountpoint": mp,
                            "fstype": block.get("fstype", ""),
                            "size": block.get("size", ""),
                        })
                        seen_mounts.add(mp)
                walk(block.get("children") or [], tran)

        walk(data.get("blockdevices") or [])
        devices.extend(TreePanel._detect_mtp_devices())
        devices.sort(key=lambda d: d["label"])
        return devices

    @staticmethod
    def _detect_mtp_devices() -> list[dict]:
        """Return phones/cameras attached over MTP (they never show in lsblk).

        Parses `gio mount -li`, which lists MTP volumes whether or not
        they're mounted yet. Already-mounted ones are matched against the
        live gvfs FUSE dir to get a real filesystem path; unmounted ones
        get mountpoint=None and an activation_root so a click can mount
        them on demand.
        """
        try:
            result = subprocess.run(
                ["gio", "mount", "-li"], capture_output=True, text=True, timeout=5,
            )
            output = result.stdout
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
                FileNotFoundError):
            return []

        gvfs_dir = f"/run/user/{os.getuid()}/gvfs" if hasattr(os, "getuid") else None
        try:
            mounted_dirs = os.listdir(gvfs_dir) if gvfs_dir and os.path.isdir(gvfs_dir) else []
        except OSError:
            mounted_dirs = []

        devices: list[dict] = []
        name = None
        activation_root = None
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("Volume(") and "MTP" not in line:
                name = None  # non-MTP volume block, ignore until next Volume(
            if line.startswith("Volume(") and ":" in line:
                name = line.split(":", 1)[1].strip()
                activation_root = None
            elif line.startswith("Type:") and "MTP" not in line:
                name = None  # confirmed non-MTP, skip this block
            elif line.startswith("activation_root=") and name is not None:
                activation_root = line.split("=", 1)[1].strip()
                host = activation_root.rstrip("/").rsplit("/", 1)[-1]
                mountpoint = None
                for d in mounted_dirs:
                    if host in d or d.replace("mtp:host=", "") in host:
                        mountpoint = os.path.join(gvfs_dir, d)
                        break
                devices.append({
                    "label": name,
                    "mountpoint": mountpoint,
                    "fstype": "mtp",
                    "size": "",
                    "activation_root": None if mountpoint else activation_root,
                })
                name = None
        return devices

    @staticmethod
    def _gio_mount_mtp(activation_root: str) -> str | None:
        """Run `gio mount`; None on success, else the failure detail."""
        try:
            result = subprocess.run(
                ["gio", "mount", activation_root],
                capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            return _MOUNT_TIMEOUT
        except FileNotFoundError:
            return _MOUNT_NO_GIO
        if result.returncode == 0:
            return None
        return (result.stderr or result.stdout).strip() or _MOUNT_TIMEOUT

    @staticmethod
    def _mtp_device_node(detail: str) -> str | None:
        """Extract /dev/bus/usb/BBB/DDD from gio's `MTP device "002,017"` wording."""
        match = re.search(r"MTP device\D+(\d+)\s*,\s*(\d+)", detail)
        if not match:
            return None
        return f"/dev/bus/usb/{int(match.group(1)):03d}/{int(match.group(2)):03d}"

    @staticmethod
    def _usb_device_holders(node: str) -> list[tuple[int, str]]:
        """(pid, name) of processes with the USB node open.

        Reads /proc directly rather than shelling out to lsof/fuser: those need
        root to see other users' processes and are not installed everywhere,
        while an MTP conflict is always another desktop process of this user.
        """
        holders = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            fd_dir = f"/proc/{entry}/fd"
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue  # not ours, or exited mid-scan
            for fd in fds:
                try:
                    if os.readlink(os.path.join(fd_dir, fd)) != node:
                        continue
                    with open(f"/proc/{entry}/comm") as handle:
                        holders.append((int(entry), handle.read().strip()))
                except OSError:
                    continue
                break
        return holders

    @staticmethod
    def _release_usb_device(node: str, holders: list[tuple[int, str]], timeout: float = 3.0):
        """Ask the holding processes to exit, then wait for the node to free up."""
        for pid, _ in holders:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not TreePanel._usb_device_holders(node):
                return
            time.sleep(0.1)

    @staticmethod
    def _mount_failure_message(detail: str, holders: list[tuple[int, str]]) -> str:
        if detail == _MOUNT_NO_GIO:
            return "GIO is not installed, so the MTP phone cannot be mounted."
        if detail == _MOUNT_TIMEOUT:
            return (
                "The phone did not respond in time. Unlock it, select File Transfer / MTP, and try again."
            )
        message = (
            "Could not connect to the phone. Unlock it and select File Transfer / MTP. "
            "If the phone is open in Dolphin or another file manager, close that phone view first "
            "because MTP permits only one active client."
        )
        if holders:
            names = ", ".join(sorted({f"{comm} (pid {pid})" for pid, comm in holders}))
            message = (
                f"Could not connect to the phone: {names} already has it open, "
                "and MTP permits only one active client. Close that program and try again."
            )
        return f"{message}\n\nGIO: {detail}" if detail else message

    @staticmethod
    def _smb_mountpoint(server: str, share: str) -> str | None:
        gvfs_dir = f"/run/user/{os.getuid()}/gvfs" if hasattr(os, "getuid") else ""
        try:
            entries = os.listdir(gvfs_dir)
        except OSError:
            return None
        wanted_server = server.rstrip(".").casefold()
        wanted_share = share.casefold()
        for entry in entries:
            if not entry.startswith("smb-share:"):
                continue
            values = {}
            for field in entry[len("smb-share:"):].split(","):
                key, separator, value = field.partition("=")
                if separator:
                    values[key] = unquote(value)
            mounted_server = values.get("server", "").rstrip(".").casefold()
            mounted_share = values.get("share", "").casefold()
            if mounted_server == wanted_server and mounted_share == wanted_share:
                path = os.path.join(gvfs_dir, entry)
                if os.path.isdir(path):
                    return path
        return None

    @staticmethod
    def smb_uri_for_path(path: str) -> str | None:
        gvfs_dir = f"/run/user/{os.getuid()}/gvfs" if hasattr(os, "getuid") else ""
        try:
            relative = os.path.relpath(path, gvfs_dir)
        except ValueError:
            return None
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            return None
        mount_name, separator, inner = relative.partition(os.sep)
        if not mount_name.startswith("smb-share:"):
            return None
        values = {}
        for field in mount_name[len("smb-share:"):].split(","):
            key, equals, value = field.partition("=")
            if equals:
                values[key] = unquote(value)
        server, share = values.get("server"), values.get("share")
        if not server or not share:
            return None
        uri = f"smb://{quote(server, safe='.-:[]')}/{quote(share, safe='')}"
        if separator and inner:
            uri += "/" + "/".join(quote(part, safe="") for part in inner.split(os.sep))
        return uri
