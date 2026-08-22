# TASKS.md

Bite-sized implementation checklist. Check off each task as completed.

---

## Setup - [COMPLETED] ✓

- [x] Create `src/` package directory with `__init__.py`
- [x] Create `state/` directory with starter JSON files
- [x] Write `requirements.txt` (PyQt6, pytest)
- [x] Write `main.py` entry point

---

## Phase 1 — Core Shell - [COMPLETED] ✓

- [x] `src/main_window.py` — MainWindow with menu bar, toolbar, status bar
- [x] `src/tree_panel.py` — QTreeWidget with lazy directory expansion
- [x] `src/file_list.py` — QTableView backed by QAbstractTableModel + QSortFilterProxyModel
- [x] `src/file_pane.py` — FilePaneWidget: address bar + filter + tree + file list
- [x] Tree click → file list navigates to selected directory
- [x] Double-click dir in file list → navigate into it
- [x] `src/actions.py` — create_directory, create_file, rename_path, delete_path, Actions class
- [x] Create Directory (Ctrl+Shift+N)
- [x] Create File with extension prompt (Ctrl+T)
- [x] Rename via F2 / menu
- [x] Copy/Move to other pane (F5/F6)
- [x] Delete with confirm dialog (Delete key)

---

## Phase 2 — Navigation & Filtering - [COMPLETED] ✓

- [x] Filter bar at top of each pane (live filter via QSortFilterProxyModel)
- [x] Back / Up / Home navigation buttons in each pane
- [x] Address bar (editable, Enter to navigate)
- [x] `src/search.py` — recursive search with wildcard support (Ctrl+F)
- [x] `src/bookmarks.py` — BookmarkManager + optional UI panel
- [x] Bookmark current dir (Ctrl+B) — persists across restarts
- [x] Bookmarks appear in Bookmarks menu

---

## Phase 3 — Dual Pane & Columns - [COMPLETED] ✓

- [x] F3 toggles single/dual pane
- [x] Each pane independently tracks its current directory
- [x] Each pane's last directory saved to session.json; restored on launch
- [x] Sortable columns (click header → ascending/descending)
- [x] Dirs sorted before files; Name/Size/Modified/Type columns

---

## Phase 4 — Hotkeys & Bash Actions - [COMPLETED] ✓

- [x] `src/settings.py` — Settings dialog with General / Hotkeys / Bash Actions tabs
- [x] Hotkeys tab: editable table of action → key sequence
- [x] Hotkeys persisted to `state/hotkeys.json`
- [x] Bash Actions tab: name, command, hotkey — executes in active pane's cwd
- [x] Bash actions persisted across restarts
- [x] `src/state_manager.py` — unified state manager for all JSON state

---

## Phase 5 — Polish - [COMPLETED] ✓

- [x] Window title updates to show active pane's current directory
- [x] Status bar: item count, selected count, free disk space
- [x] Permission errors shown in dialog; app does not crash
- [x] All tests pass (0 failures, 0 skipped)

---

## Phase 6 — Tabs & Layout - [COMPLETED] ✓

- [x] Tab bar per pane — new (Ctrl+T), close (Ctrl+W), cycle (Ctrl+Tab)
- [x] Tabs reorderable by drag; order and current tab restored on launch
- [x] Per-tab back/forward history
- [x] Address bar with shell-style Tab completion
- [x] Trees toggleable per pane (Ctrl+\); splitter sizes persisted
- [x] Window geometry, toolbar state and font persisted

## Phase 7 — Devices - [COMPLETED] ✓

- [x] `src/tree_panel.py` — MTP device detection and mount via GVFS
- [x] SMB share mount via `gio mount`, credentials on stdin (never argv)
- [x] `src/credential_store.py` — SMB credentials in KDE Wallet
- [x] SMB bookmarks persisted as `smb://` URIs, re-mounted on click
- [x] `tests/test_mtp_mount.py`
- [x] Free a phone held by KDE's KIO worker by evicting only the MTP
      backends, so a running adb server no longer blocks the mount

## Phase 8 — Transfers - [COMPLETED] ✓

- [x] `src/transfer.py` — background copy with progress, cancel, SHA-256
- [x] MTP destinations routed through `gio copy`
- [x] Optional post-transfer verification pass
- [x] `tests/test_mtp_transfer.py`

## Phase 9 — Git Integration - [COMPLETED] ✓

- [x] `src/git/provider.py` — porcelain=v2 parsing, plain Python, no Qt
- [x] `src/vcs/` — VCS-agnostic manager, cache, worker, status types
- [x] Git column in the file list; repo/dirty badges in the tree
- [x] Current branch in the status bar
- [x] `docs/git-architecture.md`
- [x] `tests/test_git_provider.py`, `test_repo_cache.py`, `test_vcs_manager.py`

## Phase 10 — Public Release - [COMPLETED] ✓

- [x] Purge caches; merge `test/` into `tests/`
- [x] Untrack all per-user state (`state/*.json`) and `.claude/`
- [x] Security audit against the code — `docs/security-audit.md`
- [x] Fix tar path traversal in archive extraction (+ `tests/test_extract_safety.py`)
- [x] Portable `traverse.desktop`; MIT `LICENSE`
- [x] README, PLANS, TASKS, MAP and success criteria matched to the build
- [x] Screenshots rendered offscreen from a synthetic demo tree, with
      `screenshots/generate.py` committed so they can be reproduced
- [x] Squash to a single initial commit (pre-squash history kept in a local
      bundle outside the repo)
- [x] Push to GitHub (private)
