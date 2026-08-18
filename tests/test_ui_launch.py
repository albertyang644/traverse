"""UI smoke tests: verify Qt app and MainWindow instantiate without crashing.

Requires a display. In headless CI, run with:
    QT_QPA_PLATFORM=offscreen pytest tests/test_ui_launch.py
"""

import os
import sys
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_qapplication_creates(qapp):
    assert qapp is not None


def test_main_window_instantiates(qapp):
    from src.main_window import MainWindow
    win = MainWindow()
    assert win is not None
    win.close()


def test_main_window_has_menu_bar(qapp):
    from src.main_window import MainWindow
    win = MainWindow()
    assert win.menuBar() is not None
    win.close()


def test_main_window_has_status_bar(qapp):
    from src.main_window import MainWindow
    win = MainWindow()
    assert win.statusBar() is not None
    win.close()


def test_file_pane_instantiates(qapp):
    from src.file_pane import FilePane
    pane = FilePane()
    assert pane is not None


def test_tree_panel_instantiates(qapp):
    from src.tree_panel import TreePanel
    panel = TreePanel()
    assert panel is not None


def test_file_list_instantiates(qapp):
    from src.file_list import FileListPane
    pane = FileListPane()
    assert pane is not None


def test_file_list_rubber_band_selects_rows_from_whitespace(qapp, tmp_path):
    from PyQt6.QtCore import QPoint, Qt
    from src.file_list import FileListPane

    for name in ("alpha.txt", "beta.txt", "gamma.txt"):
        (tmp_path / name).write_text(name)

    pane = FileListPane()
    pane.navigate(str(tmp_path))
    pane.resize(700, 400)
    pane.show()
    qapp.processEvents()

    first = pane.table.visualRect(pane.table.model().index(0, 0))
    second = pane.table.visualRect(pane.table.model().index(1, 0))
    whitespace = QPoint(pane.table.viewport().width() - 2, second.center().y())
    pane._begin_rubber_band(whitespace, Qt.KeyboardModifier.NoModifier)
    pane._update_rubber_band(QPoint(20, first.top()))

    assert {idx.row() for idx in pane.table.selectionModel().selectedRows()} == {0, 1}
    pane._rubber_band.hide()
    pane.close()


def test_enter_on_highlighted_file_launches_default(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    import src.file_pane as file_pane
    from src.file_pane import FilePaneWidget
    from src.tree_panel import TreePanel

    target = tmp_path / "example.txt"
    target.write_text("hello")
    launched = []

    def fake_popen(cmd):
        launched.append(cmd)

    # Device detection shells out during TreePanel construction; with Popen
    # stubbed, subprocess.run inside it would fail before the test starts.
    monkeypatch.setattr(TreePanel, "_detect_devices", staticmethod(lambda: []))
    monkeypatch.setattr(file_pane.subprocess, "Popen", fake_popen)

    pane = FilePaneWidget(start_dir=str(tmp_path))
    pane.file_list.table.setFocus()
    index = pane.file_list.table.model().index(0, 0)
    pane.file_list.table.setCurrentIndex(index)
    pane.file_list.table.selectRow(0)

    QTest.keyClick(pane.file_list.table, Qt.Key.Key_Return)

    assert launched == [["xdg-open", str(target)]]
    pane.close()


def test_tree_navigation_updates_file_list_in_new_tab(qapp, tmp_path, monkeypatch):
    from src.file_pane import FilePaneWidget
    from src.tree_panel import TreePanel

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "visible.txt").write_text("hello")
    monkeypatch.setattr(TreePanel, "_detect_devices", staticmethod(lambda: []))

    pane = FilePaneWidget(start_dir=str(first))
    pane.new_tab()
    pane.tree.dir_selected.emit(str(second))

    assert pane.current_dir == str(second)
    assert pane.file_list.current_dir == str(second)
    assert pane.file_list.item_count() == 1
    assert pane._tabs[pane._tabbar.currentIndex()]["path"] == str(second)
    pane.close()


def test_address_enter_navigates_and_clears_stale_filter(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from src.file_pane import FilePaneWidget
    from src.tree_panel import TreePanel

    first = tmp_path / "first"
    destination = tmp_path / "puebla"
    first.mkdir()
    destination.mkdir()
    (destination / "Puebla.kml").write_text("map")
    monkeypatch.setattr(TreePanel, "_detect_devices", staticmethod(lambda: []))

    pane = FilePaneWidget(start_dir=str(first))
    pane._filter.setText("core")
    partial = str(tmp_path / "pue")
    pane._addr.setText(partial)
    pane._addr.setCursorPosition(len(partial))
    pane._addr.setFocus()
    QTest.keyClick(pane._addr, Qt.Key.Key_Tab)
    assert pane._addr.text() == str(destination) + os.sep
    QTest.keyClick(pane._addr, Qt.Key.Key_Return)

    assert pane.current_dir == str(destination)
    assert pane.file_list.current_dir == str(destination)
    assert pane._filter.text() == ""
    assert pane.file_list.item_count() == 1
    assert pane.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == str(destination)
    pane.close()


def test_address_enter_wins_over_main_window_return_shortcut(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from src.main_window import MainWindow
    from src.tree_panel import TreePanel

    start = tmp_path / "start"
    destination = tmp_path / "destination"
    start.mkdir()
    destination.mkdir()
    (destination / "result.txt").write_text("visible")
    monkeypatch.setattr(TreePanel, "_detect_devices", staticmethod(lambda: []))

    window = MainWindow()
    window.show()
    qapp.processEvents()
    pane = window._left
    pane.navigate(str(start))
    pane._addr.setText(str(destination))
    pane._addr.setCursorPosition(len(pane._addr.text()))
    pane._addr.setFocus()
    QTest.keyClick(pane._addr, Qt.Key.Key_Return)

    assert pane.current_dir == str(destination)
    assert pane.file_list.item_count() == 1
    window.close()


def test_address_navigation_materializes_directory_missing_from_tree(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from src.file_pane import FilePaneWidget
    from src.tree_panel import TreePanel

    start = tmp_path / "start"
    start.mkdir()
    monkeypatch.setattr(TreePanel, "_detect_devices", staticmethod(lambda: []))
    pane = FilePaneWidget(start_dir=str(start))

    # Hidden and created after tree construction: deliberately absent from
    # the tree's initial snapshot.
    destination = tmp_path / ".late-directory"
    destination.mkdir()
    (destination / "visible.txt").write_text("content")
    assert str(destination) not in pane.tree._items_by_path

    pane._addr.setText(str(destination))
    pane._addr.setCursorPosition(len(pane._addr.text()))
    QTest.keyClick(pane._addr, Qt.Key.Key_Return)

    assert pane.current_dir == str(destination)
    assert pane.file_list.item_count() == 1
    assert pane.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == str(destination)
    assert str(destination) in pane.tree._items_by_path
    pane.close()


def test_moving_tabs_keeps_navigation_state_aligned(qapp, tmp_path, monkeypatch):
    from src.file_pane import FilePaneWidget
    from src.tree_panel import TreePanel

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(TreePanel, "_detect_devices", staticmethod(lambda: []))

    pane = FilePaneWidget(start_dir=str(first))
    pane.new_tab(str(second))
    pane._tabbar.moveTab(1, 0)
    pane._tabbar.setCurrentIndex(1)

    assert pane.current_dir == str(first)
    assert pane._tabs[0]["path"] == str(second)
    assert pane._tabs[1]["path"] == str(first)
    pane.close()
