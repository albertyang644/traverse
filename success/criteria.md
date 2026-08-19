# Success Criteria

This file defines when each phase of Traverse is considered complete.

---

## Phase 1 — Core Shell ✓

- [x] `python main.py` launches without errors on Kubuntu/KDE
- [x] Main window appears with a menu bar and status bar
- [x] Left directory tree displays the filesystem (lazy-loaded QTreeWidget)
- [x] File list panel shows files and directories for the selected tree node
- [x] Clicking a directory in the tree updates the file list
- [x] Double-clicking a directory in the file list navigates into it
- [x] File list shows: Name, Size, Modified, Type columns (sortable)
- [x] Create Directory works (Ctrl+Shift+N)
- [x] Create File works (Ctrl+T), prompts for name/extension
- [x] Rename works via F2 / Edit menu
- [x] Delete works with confirm dialog

---

## Phase 2 — Navigation & Filtering ✓

- [x] Filter bar at top of each pane — live filter as user types
- [x] Filter is case-insensitive
- [x] Ctrl+F opens search; results shown in a dialog
- [x] Search works recursively into subdirectories
- [x] Search supports `*` and `?` wildcards
- [x] Bookmark current directory via Ctrl+B (persists across restart)
- [x] Remove a bookmark (via Bookmarks menu / settings)
- [x] Click a bookmark navigates the active pane
- [x] Back (←), Up (↑), Home (⌂) buttons in each pane

---

## Phase 3 — Dual Pane & Columns ✓

- [x] F3 toggles single/dual pane
- [x] Each pane operates independently (different directories)
- [x] Each pane's last directory is remembered across restarts
- [x] Clicking a column header sorts ascending; clicking again sorts descending
- [x] Directories always sorted above files

---

## Phase 4 — Hotkeys & Bash Actions ✓

- [x] Settings dialog opens (Ctrl+,)
- [x] Every built-in action has a displayed hotkey in Settings > Hotkeys
- [x] Hotkeys are editable in settings and persist across restarts
- [x] User can define a Bash action: name + shell command + hotkey
- [x] Bash action executes in the current pane's directory
- [x] Bash actions persist across restarts

---

## Phase 5 — Polish ✓

- [x] Window title reflects the active pane's current directory
- [x] Status bar shows: item count, selected count, free disk space
- [x] Permission errors show a dialog; app does not crash
- [x] The full test suite passes

---

---

## Phase 6 — Tabs, Devices, Transfers, Git ✓

- [x] Each pane has tabs; tab set, order and per-tab history survive a restart
- [x] An MTP phone appears in the tree and mounts on click
- [x] An SMB share mounts, and its credentials can be saved to KDE Wallet
- [x] Copying to a phone shows progress and can be cancelled
- [x] Optional SHA-256 verification reports a mismatch instead of failing silently
- [x] Git status shows per file, per directory and in the status bar, and
      navigating a large repo never blocks the UI

---

## Phase 7 — Public Release ✓

- [x] A fresh clone runs with no `state/` directory at all
- [x] No personal path, bookmark or hostname is present in the working tree
- [x] Extracting a hostile archive cannot write outside the target directory
- [x] `traverse.desktop` works after one documented substitution
- [x] README describes the shipped build, not the Phase 1 build
- [x] README screenshots show the real UI and contain nothing from the
      developer's machine; `screenshots/generate.py` regenerates them

---

## Final Gate ✓

All tasks in `TASKS.md` are checked off, apart from the two release steps that
are the maintainer's call (history handling and the push itself).
Test suite: **90 passed, 0 failed, 0 skipped** (`python3 -m pytest tests/ -q`).
