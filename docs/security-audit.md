# Security Audit — 2026-08-17

Scope: the whole repo at the point it was prepared for a public GitHub push.
Threat model: Traverse is a desktop file manager run by one local user. It has
no network listener and no privilege boundary of its own. The realistic threats
are therefore (a) *content* the user did not write — filenames, archives, remote
mounts — reaching a shell or a path, and (b) the developer's own data leaking
into a published repo.

Every finding below was reproduced or ruled out against the code, not taken on
report. This supersedes the earlier `qwen_audit.md`, whose findings are
re-adjudicated at the bottom.

---

## Fixed in this pass

### 1. Archive extraction wrote outside the destination (tar path traversal)

`FilePaneWidget.extract_archives` / `extract_archives_to_folder` called
`shutil.unpack_archive(archive, dest)`. `zipfile` sanitises member names, but
`tarfile` on Python 3.12 does not: a member named `../escaped.txt`, or a symlink
pointing out of the tree, is honoured. Reproduced — extracting a crafted tar
wrote a file one level above the chosen directory. A downloaded `.tar.gz` was
enough to drop a file into `~/.config`, `~/.bashrc`, or anywhere else the user
can write.

Fixed by routing both call sites through `FilePaneWidget._unpack`, which passes
`filter="data"` (falling back for Pythons that lack the keyword) and by catching
`tarfile.TarError` so a refused archive shows a dialog instead of crashing.
Covered by `tests/test_extract_safety.py`.

### 2. Personal data in tracked state files

`state/bookmarks.json`, `columns.json`, `defaults.json` and `hotkeys.json` were
tracked. Between them they published the developer's home-directory layout,
private project names, and installed-application paths. None of it is
configuration anyone else can use — the built-in defaults live in
`src/settings.py`, and the app writes these files on first run.

All of `state/*.json` is now untracked and gitignored, and `.claude/` with it.
`tests/test_state.py` no longer asserts that repo state files exist; it
exercises `StateManager` against a temporary directory instead, including the
fresh-checkout case where `state/` does not exist at all.

**Not fixed: the git history still contains them.** See "Open decision" below.

### 3. Hardcoded developer path in `traverse.desktop`

`Exec=` pointed at the developer's own checkout path, which is both a
leak and broken for everyone else. Now `INSTALL_DIR/main.py` with the substitution
documented in the README. A stray `%F` was not added, since `main.py` does not
accept a path argument.

---

## Reviewed and accepted (no change)

**Bash actions run through `shell=True`** (`main_window.py:1205`). This is the
feature: the user types a shell command into Settings and binds it to a hotkey.
The string comes from the user's own settings dialog and runs with their own
privileges — the same trust level as typing it into a terminal. The only way it
becomes an escalation is if `state/hotkeys.json` is attacker-writable, which
means the attacker already has the account. Documented in the README rather
than removed.

**Every other subprocess call passes an argument list, never a shell string** —
`gio mount`, `gio copy --`, `du -sb --`, `git status`, `xdg-mime`, `xdg-open`,
terminal launches. A filename containing `;`, `$(…)` or a newline cannot reach a
shell through any of them. `_open_terminal` shell-quotes its path with
`shlex.quote` for the one `xterm -e` fallback that does use a shell string.

**SMB credentials never appear in argv** (`tree_panel.py:663`): username, domain
and password go to `gio mount` on stdin, so they are not visible in `ps`. The
share URI is percent-encoded before interpolation. At rest they go to KDE
Wallet, which is the encryption boundary; the base64 in `credential_store.py` is
a transport encoding for the JSON blob, not a security measure, and is not
claimed to be one.

**Path sanitisation in `src/actions.py`** — the earlier audit called for
`Path.resolve()` against `../` attacks. It has no basis here: paths originate
from the user's own selection in the file list or a dialog, and resolving them
would break the legitimate case of operating on a symlinked directory. There is
no untrusted caller to defend against.

**No secrets in git history.** Scanned every reachable commit for private keys,
`ghp_`/`sk-`/`xox*`/AWS-shaped tokens: none. The one hit that looks like a key,
`OPENAI_API_KEY=ollama` in an old `state/defaults.json`, is the literal
placeholder a local Ollama instance requires — not a credential.

**Static analysis agrees.** `bandit -r src/ main.py` reports exactly one HIGH:
the `shell=True` in `main_window.py` that runs user-defined bash actions,
covered above. Everything else it flags is informational (B404/B603/B607 fire
on every use of `subprocess`, whether or not a shell is involved) plus one
`try/except/pass` in `toolbar.py`. Method note: this audit is a manual read of
the ~8,000 lines of source plus that static pass. It is not a fuzzing campaign,
and the UI-logic paths that never touch untrusted input got proportionally less
attention than the ones that do.

---

## Open decision: history rewrite

The working tree is clean, but commits back to the start of the project contain
`state/session.json` and `state/bookmarks.json` — full per-tab browsing history
and bookmark lists naming private directories and client projects. Publishing
the repo publishes those commits.

**Resolved:** squashed to a single initial commit before the first push. The
pre-squash history is kept in a local bundle outside the repo. Every blob in
every published commit was then re-scanned: the only `/home/...` paths that
remain are the placeholder `/home/user/...` in tests, and the only personal
name is the copyright line in LICENSE.

One residual, by design: commit metadata carries the author's real email
address, which becomes public along with the repository.

---

## Re-adjudicating `qwen_audit.md`

That report claimed 44 passing tests (the suite is 88 after this pass), and
several of its findings do not hold up:

- Its #1 (path sanitisation) and #2 (permission pre-checks on file creation) are
  not real issues — see above. Pre-flight permission checks are also a TOCTOU
  pattern; letting `open()` raise and reporting the error is correct.
- Its #3 (KDE Wallet crashes) is wrong: every DBus call in `credential_store.py`
  is already inside `try/except` returning `None`/`False`.
- Its #4–#9 are performance and UX observations, not security findings.

Its genuinely useful items are quality work, not vulnerabilities, and are left
for the backlog: the unbounded icon cache in `file_list.py`, the git status
timeout, and watcher cleanup in `vcs/manager.py`. Notably, it did not find the
one real vulnerability in the codebase (#1 above).
