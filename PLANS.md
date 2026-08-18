# PLANS.md

## Project: Traverse — KDE/Qt File Manager

A simplified xplorer2-style file manager for KDE using Qt/PyQt6 (or PySide6).

---

## Technology Stack

- **Language:** Python 3
- **UI Framework:** PyQt6 (or PySide6 as fallback)
- **Backend:** Native Python (os, pathlib, shutil)
- **Config/State:** JSON files in `state/` (bookmarks, pane history, column prefs, hotkeys)
- **No Docker, no web interface**

---

## Architecture

```
traverse/
├── main.py                  # Entry point — launches MainWindow
├── src/
│   ├── __init__.py
│   ├── main_window.py       # MainWindow: toolbar, panels, statusbar
│   ├── file_pane.py         # Single file pane (tree + file list)
│   ├── file_list.py         # QTableView with sortable columns
│   ├── tree_panel.py        # Left-side directory tree (QTreeView)
│   ├── filter_bar.py        # Filter/search bar widget
│   ├── bookmarks.py         # Bookmark sidebar/manager
│   ├── toolbar.py           # Quick-launch icon toolbar
│   ├── actions.py           # All QAction definitions + hotkey bindings
│   ├── settings.py          # Settings dialog (hotkeys, columns, behavior)
│   ├── search.py            # Recursive search engine (simple + wildcard)
│   └── state_manager.py     # Persist/restore state (pane dirs, layout)
├── state/                   # Per-user runtime JSON, untracked
│   ├── bookmarks.json
│   ├── hotkeys.json
│   ├── columns.json
│   ├── defaults.json        # Default app per MIME type
│   └── session.json         # Panes, tabs, history, geometry, font
├── docs/
└── tests/
```

Icons come from the active KDE theme (`QIcon.fromTheme`), so no `assets/`
directory was needed. The delivered `src/` also contains `transfer.py`,
`open_with.py`, `credential_store.py`, and the `vcs/` + `git/` packages —
see the layout table in README.md for the current, authoritative list.

---

## Feature Plan (Phased)

### Phase 1 — Core Shell (MVP) — done
- [x] Main window with menu bar and status bar
- [x] Left tree panel (QTreeView, filesystem model)
- [x] Single file pane (QTableView) with Name, Size, Modified, Type columns
- [x] Double-click to navigate into directory
- [x] Basic CRUD: create dir, create file, rename (long-click or F2), copy, move, delete
- [x] Delete confirm toggle in settings

### Phase 2 — Navigation & Filtering — done
- [x] Filter bar at top (live filter on visible files)
- [x] Integrated recursive search (simple string + `*`/`?` wildcard)
- [x] Bookmarks: add/remove/jump (persisted to `state/bookmarks.json`)
- [x] Quick-launch toolbar icons (configurable, persisted)

### Phase 3 — Dual Pane & Columns — done
- [x] Toggle dual-pane mode (horizontal split)
- [x] Each pane remembers its last directory across sessions
- [x] Add/remove visible columns (Size, Modified, Type, Permissions, etc.)
- [x] Sort by any column (click header)

### Phase 4 — Hotkeys & Bash Actions — done
- [x] Every action has a configurable hotkey
- [x] Settings dialog: hotkey editor (action → key sequence)
- [x] Bash action: user defines shell command, binds it to a hotkey
- [x] Hotkeys persisted to `state/hotkeys.json`

### Phase 5 — Polish & Docs — done
- [x] App icon, window title reflects current path
- [x] Statusbar: selected count, total items, disk usage
- [x] Error handling (permissions, missing files)
- [x] Generate user docs in `docs/`

### Phase 6 — Tabs & Layout — done
- [x] Tabs per pane: open, close, cycle, reorder, restored on launch
- [x] Per-tab history; address bar with Tab completion
- [x] Trees collapsible per pane; splitter sizes and geometry persisted

### Phase 7 — Devices — done
- [x] MTP phones/cameras detected and mounted through GVFS
- [x] SMB shares mounted via `gio mount`, credentials in KDE Wallet
- [x] SMB bookmarks stored as `smb://` URIs and re-mounted on click

### Phase 8 — Transfers — done
- [x] Background copy engine with progress and cancel
- [x] SHA-256 verification option
- [x] MTP writes routed through `gio copy` (MTP has no POSIX write path)

### Phase 9 — Git integration — done
- [x] Git status column, tree badges, branch in the status bar
- [x] Status computed off the UI thread, cached, invalidated by a watcher
- [x] Provider interface kept VCS-agnostic (`src/vcs/`) — see
      `docs/git-architecture.md`

### Phase 10 — Public release — done
- [x] Per-user state untracked; no personal paths in the working tree
- [x] Security audit written against the code (`docs/security-audit.md`);
      tar path-traversal fixed and covered by a test
- [x] MIT LICENSE; portable `traverse.desktop`; README matched to the build

---

## Success Criteria

1. Launches without error on KDE/Kubuntu
2. Browse filesystem in single and dual pane
3. CRUD operations work reliably
4. Filter and search work on current dir tree
5. Bookmarks persist across restarts
6. Hotkeys configurable and functional
7. No required external services or Docker
8. Test suite passes offscreen without touching the developer's state

---

## Constraints

- Python + PyQt6 only (no Electron, no web UI)
- State stored in flat JSON, not a database
- Each module must be independently testable
