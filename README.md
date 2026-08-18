# Traverse

A dual-pane, xplorer2-style file manager for KDE, written in Python + PyQt6.

Traverse is a working daily-driver file manager, not a demo: tabs per pane,
a device tree that mounts phones and SMB shares, live git status, verified
transfers, and a hotkey for every action — including shell commands you define
yourself.

---

## Features

**Panes, tabs, navigation**
- Single or dual pane (`F3`), each pane fully independent
- Tabs per pane, reorderable, restored on the next launch
- Directory tree per pane, collapsible (`Ctrl+\`)
- Editable address bar with shell-style Tab completion
- Back / Forward / Up / Home, and per-tab history
- Bookmarks menu (`Ctrl+B` to add) plus a quick-launch button toolbar

**Files**
- Create, rename (`F2`, inline), copy, cut, paste, duplicate, delete
- Copy / move to the other pane (`F5` / `F6`), or to a chosen directory
- Drag and drop between panes, with copy vs. move honoured
- Archive extraction (zip/tar/tgz/tbz2/txz) here or into a new folder, and
  archive creation from a selection
- Open with the system default, or pick an application and remember the choice
  per MIME type
- Properties, permissions, owner/group, SHA-256 checksum, copy path
- Directory sizes on demand (`Ctrl+D`), computed in the background
- Live refresh when another program changes the directory

**Columns and filtering**
- Ten columns — Name, Size, Modified, Created, Type, Extension, Permissions,
  Owner, Group, Git — each toggleable, reorderable and sortable, with
  directories always sorted above files
- Per-pane live filter bar
- Recursive search (`Ctrl+F`) with `*` and `?` wildcards, streaming results
  into a non-blocking window

**Devices**
- MTP phones and cameras, mounted through GVFS
- SMB shares, with credentials stored in KDE Wallet
- Transfers to MTP go through `gio copy`, with progress and optional
  SHA-256 verification

**Git**
- Per-file status column, per-directory badges in the tree, current branch in
  the status bar
- Status is computed off the UI thread and cached; navigation never blocks

**Customisation**
- Every action's hotkey is editable in Settings (`Ctrl+,`) and persisted
- User-defined bash actions: name + shell command + hotkey, run in the active
  pane's directory
- Font family and size; window geometry and splitter positions are remembered

---

## Requirements

- Python 3.10+
- PyQt6 (`python3-pyqt6` on Debian/Ubuntu, or `pip install PyQt6`)
- Optional, for the features that use them:
  - `gvfs` / `gio` — MTP phones and SMB shares
  - `dbus-python` + KDE Wallet — saving SMB credentials
  - `git` — the git status column
  - `xdg-utils` — MIME detection and default-application launching

## Running

```bash
pip install -r requirements.txt
python3 main.py
```

To add it to the KDE application menu, substitute the checkout path into the
desktop entry:

```bash
sed "s|INSTALL_DIR|$PWD|" traverse.desktop > ~/.local/share/applications/traverse.desktop
```

## Tests

```bash
python3 -m pytest tests/ -q      # 90 tests, Qt runs offscreen
```

`tests/conftest.py` points `TRAVERSE_STATE_DIR` at a throwaway directory, so
running the suite never touches your real bookmarks, tabs or window layout.

---

## Configuration and state

Everything Traverse remembers lives in JSON under `state/`, written on exit and
recreated on first run — nothing there is tracked in git:

| File | Holds |
|------|-------|
| `bookmarks.json` | Bookmarked directories and SMB URIs |
| `hotkeys.json`   | Hotkey overrides and bash actions |
| `columns.json`   | Per-pane column visibility, order, width, sort |
| `defaults.json`  | Default application per MIME type |
| `session.json`   | Pane directories, tabs, history, geometry, font |

Built-in hotkey defaults live in `src/settings.py`. Set `TRAVERSE_STATE_DIR` to
run against a different state directory.

### A note on bash actions

Bash actions execute the command string you type through a shell, in the active
pane's directory, with your privileges. That is the point of the feature — it is
the same trust level as typing the command into a terminal — but it does mean
`state/hotkeys.json` should be treated as executable configuration. Everything
else Traverse runs is invoked as an argument list, so filenames never reach a
shell.

---

## Default hotkeys

| Key | Action |
|-----|--------|
| `Ctrl+Shift+N` | New folder |
| `Ctrl+N` | New file |
| `Ctrl+T` / `Ctrl+W` | New tab / close tab |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | Next / previous tab |
| `F2` | Rename |
| `Delete` | Delete |
| `F5` / `F6` | Copy / move to other pane |
| `Ctrl+C` / `Ctrl+X` / `Ctrl+V` | Copy / cut / paste |
| `Ctrl+A` | Select all |
| `Ctrl+R` | Refresh |
| `F3` | Toggle dual pane |
| `Ctrl+\` | Toggle trees |
| `Ctrl+F` | Search |
| `Ctrl+B` | Bookmark current directory |
| `Alt+←` / `Alt+→` / `Alt+↑` / `Alt+Home` | Back / forward / up / home |
| `Ctrl+D` | Directory size of selection |
| `Ctrl+Shift+C` | Copy path |
| `Alt+Return` | Properties |
| `F4` | Open terminal here |
| `Ctrl+,` | Settings |

All of these are editable in Settings → Hotkeys.

---

## Project layout

```
traverse/
├── main.py                 # Entry point
├── src/
│   ├── main_window.py      # Window, menus, toolbars, search, bash actions
│   ├── file_pane.py        # A pane: address bar, filter, tabs, tree, list
│   ├── file_list.py        # File table model/view, columns, inline rename
│   ├── tree_panel.py       # Directory tree, MTP/SMB device mounting
│   ├── filter_bar.py       # Filter input
│   ├── toolbar.py          # Quick-launch buttons
│   ├── bookmarks.py        # Bookmark management
│   ├── actions.py          # Path-level CRUD primitives
│   ├── search.py           # Recursive wildcard search
│   ├── transfer.py         # Copy engine: progress, hashing, MTP via gio
│   ├── open_with.py        # MIME detection, .desktop parsing, app chooser
│   ├── settings.py         # Settings dialog, built-in hotkey defaults
│   ├── credential_store.py # SMB credentials in KDE Wallet
│   ├── state_manager.py    # Reads/writes everything under state/
│   ├── vcs/                # VCS-agnostic manager, cache, worker
│   └── git/                # The git provider (subprocess, no Qt)
├── docs/                   # Architecture and audit notes
├── tests/                  # pytest suite
└── state/                  # Per-user runtime state (untracked)
```

Design docs: [`docs/git-architecture.md`](docs/git-architecture.md),
[`docs/security-audit.md`](docs/security-audit.md).

## License

MIT — see [LICENSE](LICENSE).
