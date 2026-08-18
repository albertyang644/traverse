# Git Integration — Architecture

> Traverse is Python 3 + PyQt6 (not C++/Qt6 — corrected from an earlier,
> non-functional draft; see "Note on history" at the bottom). Everything
> below reflects the actual, working implementation in `src/vcs/` and
> `src/git/`.

## 1. Overall architecture

```
                          UI thread
  ┌────────────┐   ┌───────────┐   ┌────────────┐
  │ TreePanel  │   │ FileModel │   │ MainWindow │
  │ (badges    │   │ (Git      │   │ (branch in │
  │  repo/dir) │   │  column)  │   │  statusbar)│
  └─────┬──────┘   └─────┬─────┘   └─────┬──────┘
        │ status_for_dir │ status_for    │ branch_for
        └────────────────┴───────────────┘
                          │  (read-only, O(1), never blocks)
                          ▼
                 ┌──────────────────┐
                 │   VcsManager     │  <- one instance, owned by MainWindow,
                 │  (QObject)       │     shared by every pane/tab
                 └───┬─────────┬────┘
                     │         │
        ensure_tracking()   refresh() / invalidate()
                     │         │
                     ▼         ▼
        ┌───────────────┐   ┌───────────────────┐
        │QFileSystemWatch│   │ QThreadPool        │
        │ (per open dir  │   │  StatusRefreshTask │──▶ subprocess: git status
        │  + .git/HEAD/  │   │  (QRunnable)       │      --porcelain=v2 -z
        │  index/refs)   │   └─────────┬──────────┘
        └───────┬────────┘             │ finished(RepoStatus) [queued to UI thread]
                │ directoryChanged/     ▼
                │ fileChanged   ┌───────────────────┐
                └──────────────▶│  RepoStatusCache   │  QMutex-guarded dict
                                 │  repo_root -> RepoStatus (immutable) │
                                 └───────────────────┘
```

Every git subprocess call happens on a `QThreadPool` worker thread. The UI
thread only ever reads from `RepoStatusCache`, which is a plain dict swap
under a mutex — so status lookups during scrolling/painting never touch
disk or spawn a process.

## 2. Class diagram

```
VcsProvider (ABC)                      RepoStatus (frozen dataclass)
  + name                                 repo_root, branch
  + discover_root(dir) -> root|None      files: {rel_path: FileStatus}
  + read_status(root, gen) -> RepoStatus dir_status: {rel_dir: FileStatus}
  + watch_paths(root) -> [str]           generation: int
        ▲                                error: str | None
        │ implements
   GitProvider
     + discover_root: walk up for .git
     + read_status: run `git status --porcelain=v2 -z`, parse
     + watch_paths: [root, .git/HEAD, .git/index, .git/refs, .git/MERGE_HEAD]

RepoStatusCache                        StatusRefreshTask (QRunnable)
  - _by_root: {root: RepoStatus}         - provider, repo_root, generation
  - _generation: {root: int}             + run(): calls provider.read_status,
  + next_generation(root) -> int             emits signals.finished(RepoStatus)
  + store(status) -> bool (rejects stale)
  + status_for_path(root, rel) -> FileStatus

VcsManager (QObject)                   FileModel / TreePanel (UI, unchanged
  + ensure_tracking(dir, owner)          otherwise) hold a reference to
  + status_for(path) -> FileStatus       VcsManager and call read-only
  + status_for_dir(dir) -> FileStatus    query methods from data()/paint —
  + branch_for(root) -> str|None         never touch VcsProvider directly.
  + refresh(root) / invalidate(root)
  + repo_status_changed: pyqtSignal(str)
```

## 3. Data structures

- **`FileStatus`** (`src/vcs/status.py`) — enum: `CLEAN, MODIFIED, ADDED,
  DELETED, RENAMED, UNTRACKED, IGNORED, CONFLICTED, STAGED`. Carries a
  `priority` used to pick the "worst" status when aggregating a directory.
- **`RepoStatus`** — immutable snapshot: `files` (relative path → status),
  `dir_status` (pre-aggregated per-directory worst status), `branch`,
  `generation`, `error`. Immutability means a UI-thread reader either sees
  the whole old snapshot or the whole new one — never a half-updated dict.
- **`RepoStatusCache`** — `{repo_root: RepoStatus}` behind a `QMutex`.
  Generation counters let a superseded, slow refresh be dropped instead of
  overwriting a newer result that already landed.

## 4. Threading model

- **UI thread**: owns `VcsManager`, `QFileSystemWatcher`, `QTimer`s, and all
  widgets. Only calls read-only cache queries.
- **`QThreadPool` worker threads**: run `StatusRefreshTask.run()`, which
  calls `GitProvider.read_status()` — this spawns the `git status`
  subprocess and blocks *that* thread, never the UI thread. Using the pool
  (rather than one `QThread` per repo) caps concurrent git processes at the
  pool's max thread count instead of growing unbounded as tabs/repos open.
- Results cross back to the UI thread via `pyqtSignal` (Qt auto-queues the
  connection because the signal is emitted from a different thread than the
  receiver lives on) — no manual locking needed at that boundary.

## 5. Cache design

- One `RepoStatus` per repo root, replaced wholesale on refresh (see §3).
- `next_generation()` is reserved *before* a refresh starts; `store()`
  rejects any result whose generation is older than what's already
  cached. This lets `invalidate()` kick off a fresh refresh immediately
  without waiting for an in-flight one to finish or cancelling it — the
  in-flight one's result just gets discarded if it arrives after being
  superseded.
- `_root_for_dir` memoizes repository discovery per directory so repeated
  navigation into the same tree doesn't re-walk the filesystem for `.git`.

## 6. Repository discovery algorithm

`GitProvider.discover_root(dir)`: walk from `dir` upward checking
`os.path.exists(os.path.join(current, ".git"))` (a directory for a normal
repo, a file for a submodule/worktree) until found or the filesystem root is
reached. Pure `stat` calls, no subprocess — cheap enough to run on every
navigation. Memoized by `VcsManager` per absolute directory so it only
actually runs once per distinct directory visited.

## 7. Status refresh algorithm

```
ensure_tracking(dir):
    root = discover_root(dir)              # memoized, cheap
    watch dir (QFileSystemWatcher) + provider.watch_paths(root)
    if root not yet in cache: refresh(root, force=True)

refresh(root, force=False):
    if not force:
        debounce 300ms (coalesce bursts of fs events into one refresh)
    generation = cache.next_generation(root)
    submit StatusRefreshTask(provider, root, generation) to QThreadPool

on task finished(status):
    if cache.store(status):                # false if superseded
        emit repo_status_changed(root)      # UI repaints Name/Git columns
                                             # from cache — no re-read of dir
```

Refresh always re-runs a single `git status` for the whole repo (git's own
index makes this fast — typically single-digit milliseconds to tens of ms
even in large repos) rather than us trying to diff the tree ourselves. What
we optimize instead is *not calling git more than necessary*: debouncing,
generation-based staleness rejection, and per-repo caching so navigating
around inside an already-refreshed repo costs zero git invocations.

## 8. Error handling

| Scenario | Response |
|---|---|
| `git` not on PATH | `RepoStatus.error` set; files stay empty; UI shows no overlay (fails safe to "no info", not a crash) |
| `git status` non-zero exit | error captured from stderr, surfaced in `RepoStatus.error` |
| `git status` hangs | `subprocess.run(..., timeout=10)`; on timeout, error result returned, next refresh can retry |
| Directory not a repo | `discover_root` returns `None`; `status_for()` returns `CLEAN` everywhere, no error path exercised |
| Worker thread raises unexpectedly | `StatusRefreshTask.run()` wraps `read_status()` in try/except and still emits a `RepoStatus(error=...)` — a worker must never silently die or throw across the thread boundary |
| Repo removed while tracked | Next `git status` call fails (cwd gone) → error surfaced; watcher naturally stops firing since the path no longer exists |

## 9. Performance considerations

| Concern | How it's addressed |
|---|---|
| 100,000-file directory responsiveness | Status lookups are O(1) dict gets per visible row (`RepoStatus.files`/`dir_status`), done in `FileModel.data()` — no per-row disk or git access. The `git status` subprocess itself runs once per refresh regardless of directory size, off the UI thread. |
| Minimizing process creation | One `git status` process per refresh, throttled to at most once per 300ms-debounce window per repo; navigating around an already-tracked repo does not re-invoke git at all. |
| Avoiding full repo rescans | We don't rescan/walk the filesystem ourselves — `git status` already does the minimal work via its index; our own added cost is the O(1) cache reads plus one O(files) aggregation pass per refresh (`build_dir_aggregate`), not per paint. |
| Multiple repos / tabs | `RepoStatusCache` and `_root_for_dir` are keyed by repo root, independent per repo; `QThreadPool` caps concurrent subprocesses across all open repos. |
| UI never blocks | All git subprocess execution happens in `QRunnable.run()` on a pool thread; UI-thread code path (`status_for`, `status_for_dir`, `branch_for`) touches only in-memory dicts. |

## 10. C++ class skeletons

Not applicable — Traverse is Python/PyQt6, confirmed against `main.py`,
`requirements.txt`, and every existing module in `src/`. See §2 for the
actual (Python) class shapes, and `src/vcs/`, `src/git/` for the real code.

## 11. Implementation order (as built)

1. `src/vcs/status.py` — pure data structures, no dependencies.
2. `src/vcs/provider.py` — abstract interface, depends only on `status.py`.
3. `src/git/provider.py` — Git implementation + porcelain parser; testable
   with zero Qt (`test/test_git_provider.py`).
4. `src/vcs/cache.py` — thread-safe cache; depends on `status.py` + `QMutex`.
5. `src/vcs/worker.py` — `QRunnable` wrapper so git subprocess calls run off
   the UI thread.
6. `src/vcs/manager.py` — coordinator gluing discovery + cache + worker +
   `QFileSystemWatcher` + debounce.
7. UI integration: `file_list.py` (Git column + Name coloring),
   `tree_panel.py` (repo-root / dirty-folder badges), `file_pane.py`
   (wires navigation to `ensure_tracking`/`refresh`), `main_window.py`
   (owns the single shared `VcsManager`, shows branch in the status bar).
8. Tests: `test/test_git_provider.py` (parsing/discovery, no Qt),
   `test/test_repo_cache.py` (cache correctness), `test/test_vcs_manager.py`
   (end-to-end against real `git init` repos in `tmp_path`).

This order was chosen so every layer is independently testable before the
next one is built on top of it, and so the VCS-specific code
(`src/git/provider.py`) is the *only* file that knows about `git` as a
program — everything above `VcsProvider` is generic.

## 12. Design rationale

- **`QThreadPool` + `QRunnable` over one `QThread` per repo**: bounds the
  number of concurrent OS processes/threads regardless of how many repos or
  tabs are open, and reuses pool threads instead of paying thread-creation
  cost per refresh.
- **Immutable `RepoStatus` swapped wholesale, not mutated in place**: the UI
  thread can read it without a lock beyond the dict-swap mutex, and there's
  no way to observe a half-parsed status.
- **Generation counters instead of cancelling in-flight subprocesses**:
  killing a running `git status` process adds complexity (signal handling,
  partial-output races) for no real benefit — it's cheaper to let it finish
  and simply discard the result if it's stale by the time it lands.
- **Debounce on automatic refreshes, not on explicit ones**: filesystem
  events from tools like `git checkout` arrive in bursts; explicit
  user-triggered refresh (Ctrl+R) should feel instant, so `invalidate()`
  bypasses the debounce window entirely.
- **`VcsProvider` as an ABC (the one place we use inheritance)**: every
  other seam in this design is composition (`VcsManager` *has a* cache,
  *has a* thread pool, *has a* watcher), but "the code calling status logic
  shouldn't need to know which VCS answered" is exactly the polymorphism
  problem inheritance solves cleanly in Python — a `Protocol` would work
  too, but an ABC also gives us "you forgot to implement a method" errors
  at instantiation time.
- **Directory aggregation computed once per refresh, not once per paint**:
  `build_dir_aggregate()` walks each changed file's ancestor chain exactly
  once when a `RepoStatus` is built; `TreePanel`/`FileModel` then do O(1)
  dict lookups, so painting a tree with thousands of expanded folders stays
  cheap.
- **Watching only currently-open directories + `.git` metadata, not the
  whole working tree recursively**: a recursive `QFileSystemWatcher` over a
  100k-file repo would need a comparable number of OS watch descriptors
  (and can hit `inotify` limits). Watching `.git/HEAD`/`index`/`refs` covers
  every git-initiated change (commit, checkout, merge, stash), and watching
  only the directory each pane currently has open covers external edits
  proportional to what's actually on screen.

## 13. Adding another VCS later

Implement `VcsProvider` (4 methods: `name`, `discover_root`, `read_status`,
`watch_paths`) for the new backend, e.g. `src/hg/provider.py`, and pass it
into `VcsManager(providers=[GitProvider(), HgProvider()])`. `VcsManager`
already tries each provider in order per directory and caches which one
matched — no changes needed to the cache, worker, or any UI code. The only
constraint on a new provider: `read_status` must run safely on a worker
thread (no Qt widget access) and must return relative paths keyed the same
way `RepoStatus.files` expects.

## Note on history

An earlier pass at this feature was written against a hypothetical
C++/Qt6 version of Traverse that doesn't exist in this repository, and its
Python "implementation" (`src/git/`, `src/cache/`, `src/pvf/`) consisted of
duplicate class definitions, undefined names, and methods that
unconditionally returned empty/zero values — none of it executed
successfully. It also depended on a nonexistent Git "daemon" process
(`qgit`) with no basis in real Git. That code and its design doc were
deleted; this document and the code under `src/vcs/` and `src/git/`
describe what's actually implemented and tested.
