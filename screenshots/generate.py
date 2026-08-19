#!/usr/bin/env python3
"""Regenerate the README screenshots.

Renders the real widgets on Qt's offscreen platform against a synthetic
directory tree in /tmp, so the images never contain anything from the
machine that made them. No display or window manager needed:

    python3 screenshots/generate.py

Files land next to this script. The demo tree is rebuilt from scratch on
every run, so the screenshots are reproducible rather than hand-captured.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
DEMO = Path("/tmp/traverse-demo")
STATE = Path("/tmp/traverse-demo-state")


# ── The synthetic tree ────────────────────────────────────────────

def _file(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))


def build_demo_tree():
    """A plausible home directory: media, documents, and one git repo."""
    if DEMO.exists():
        subprocess.run(["rm", "-rf", str(DEMO)], check=True)

    for d in ("Documents/contracts", "Documents/invoices", "Documents/reports",
              "Music/albums/kid-a", "Music/albums/ok-computer",
              "Pictures/2026-06-lisbon", "Pictures/wallpapers/dark",
              "Pictures/wallpapers/light", "Videos/clips/raw",
              "Videos/exports/2026", "Projects/notes/2026", "Projects/notes/archive"):
        (DEMO / d).mkdir(parents=True, exist_ok=True)

    for name, size in [("ubuntu-24.04-desktop.iso.part", 240_000),
                       ("holiday-2026.zip", 3_400_000),
                       ("invoice-4471.pdf", 812_000),
                       ("podcast-ep17.mp3", 5_100_000),
                       ("sensor-log.csv", 1_200_000),
                       ("notes.txt", 96_000),
                       ("camera-roll.tar.gz", 640_000),
                       ("complete/render-final.mkv", 2_200_000)]:
        _file(DEMO / "Downloads" / name, size)

    _file(DEMO / "Pictures/2026-06-lisbon/DSC_0142.jpg", 900_000)
    _file(DEMO / "Pictures/2026-06-lisbon/DSC_0143.jpg", 700_000)
    _file(DEMO / "Documents/reports/q3-summary.odt", 60_000)
    _file(DEMO / "Documents/invoices/2026-07.pdf", 20_000)
    _file(DEMO / "Music/albums/playlist.m3u", 40_000)
    _file(DEMO / "Videos/clips/intro.mp4", 30_000)
    (DEMO / "Projects/notes/2026/standup.md").write_text("meeting notes\n")

    # A git repo, so the Git column and the tree badges have something to say.
    repo = DEMO / "Projects" / "traverse"
    (repo / "docs").mkdir(parents=True, exist_ok=True)
    (repo / "requirements.txt").write_text("PyQt6>=6.5.0\npytest>=8.0\n")
    (repo / "README.md").write_text("# Traverse\n\nDual-pane file manager.\n")
    (repo / "docs/git-architecture.md").write_text("architecture notes\n")
    for name in ("main_window", "file_pane", "file_list", "tree_panel",
                 "transfer", "settings", "open_with", "state_manager"):
        _file(repo / "src" / f"{name}.py", 41_000)
    for name in ("test_crud", "test_state", "test_search"):
        _file(repo / "tests" / f"{name}.py", 3_000)

    git = ["git", "-C", str(repo), "-c", "user.email=demo@example.com",
           "-c", "user.name=demo"]
    subprocess.run(git + ["init", "-q", "."], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "initial import"], check=True)

    # Leave the repo dirty: two modified files and one untracked.
    with (repo / "src/file_pane.py").open("a") as f:
        f.write("edited\n")
    with (repo / "src/file_list.py").open("a") as f:
        f.write("edited\n")
    _file(repo / "src/bookmarks.py", 12_000)


def seed_state():
    """Pre-set panes, columns, bookmarks and bash actions for the shots."""
    subprocess.run(["rm", "-rf", str(STATE)], check=True)
    STATE.mkdir(parents=True)

    def columns(name_width):
        return {"visible_columns": [0, 1, 2, 4, 9],
                "column_order": list(range(10)),
                "widths": {"0": name_width, "1": 70, "2": 115, "4": 60, "9": 70},
                "sort_column": 0, "sort_order": "ascending"}

    (STATE / "columns.json").write_text(
        json.dumps({"left": columns(190), "right": columns(215)}))
    (STATE / "bookmarks.json").write_text(json.dumps({"bookmarks": [
        str(DEMO / "Projects"), str(DEMO / "Downloads"),
        str(DEMO / "Pictures"), str(DEMO / "Documents")]}))
    (STATE / "session.json").write_text(json.dumps({
        "left_pane_dir": str(DEMO / "Projects" / "traverse" / "src"),
        "right_pane_dir": str(DEMO / "Downloads"),
        "font": {"family": "Fira Mono", "size": 11}}))
    (STATE / "hotkeys.json").write_text(json.dumps({"actions": {}, "bash_actions": [
        {"name": "Optimise PNGs", "command": "optipng -o3 *.png", "hotkey": "Ctrl+Shift+O"},
        {"name": "Sync to NAS", "command": "rsync -a . nas:/backup/", "hotkey": "Ctrl+Shift+Y"}]}))


# ── Rendering ─────────────────────────────────────────────────────

def main():
    build_demo_tree()
    seed_state()

    os.environ["TRAVERSE_STATE_DIR"] = str(STATE)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(REPO))

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (QApplication, QTableView, QTableWidget,
                                 QTabWidget, QTreeWidgetItemIterator)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Traverse")
    QIcon.setThemeName(os.environ.get("SCREENSHOT_ICON_THEME", "breeze"))

    def pump(ms=800):
        end = time.time() + ms / 1000
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def demo_items(tree):
        it = QTreeWidgetItemIterator(tree)
        while it.value():
            item = it.value()
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path and (path == str(DEMO) or path.startswith(str(DEMO) + "/")):
                yield path, item
            it += 1

    def frame_tree(pane):
        """Expand the demo subtree and park its root at the top of the view,
        so the rows on screen are demo content and not this machine's /tmp."""
        tree = pane.tree._dir_tree
        for _ in range(4):                     # lazy children arrive per pass
            for _path, item in list(demo_items(tree)):
                tree.expandItem(item)
            pump(300)
        for path, item in demo_items(tree):
            if path == str(DEMO):
                tree.scrollToItem(item, tree.ScrollHint.PositionAtTop)
                break

    from src.main_window import MainWindow, _SearchResultsWindow
    from src.settings import SettingsDialog

    window = MainWindow()
    window.resize(1400, 700)
    window.show()
    pump(1500)

    window._left.navigate(str(DEMO / "Projects" / "traverse" / "src"))
    window._right.navigate(str(DEMO / "Downloads"))
    pump(2500)
    window._left.file_list.refresh()
    window._right.file_list.refresh()
    pump(2500)

    for pane in (window._left, window._right):
        pane.set_splitter_sizes([160, 540])
        frame_tree(pane)

    # A selection, so the status bar shows its size readout.
    table = window._left.file_list.table
    table.selectRow(2)
    selection = table.selectionModel()
    selection.select(table.model().index(3, 0),
                     selection.SelectionFlag.Select | selection.SelectionFlag.Rows)
    pump(800)
    window.grab().save(str(OUT / "dual-pane.png"))

    results = _SearchResultsWindow(str(DEMO), "*.py", True, main_window=window, parent=window)
    results.resize(1000, 460)
    results.show()
    pump(3000)
    for table in results.findChildren(QTableWidget):
        table.sortItems(0, Qt.SortOrder.AscendingOrder)
    for view in results.findChildren(QTableView):
        view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    pump(600)
    results.grab().save(str(OUT / "search.png"))
    results.close()

    dialog = SettingsDialog(window._state, parent=window)
    dialog.resize(760, 560)
    dialog.show()
    pump(1200)
    tabs = dialog.findChild(QTabWidget)

    def show_tab(needle):
        for i in range(tabs.count()):
            if needle in tabs.tabText(i).lower():
                tabs.setCurrentIndex(i)
                return

    show_tab("hotkey")
    pump(800)
    dialog.grab().save(str(OUT / "settings-hotkeys.png"))

    show_tab("bash")
    dialog.resize(760, 380)
    pump(800)
    dialog.grab().save(str(OUT / "bash-actions.png"))
    dialog.close()

    for name in ("dual-pane", "search", "settings-hotkeys", "bash-actions"):
        print(f"wrote {OUT / (name + '.png')}")


if __name__ == "__main__":
    main()
