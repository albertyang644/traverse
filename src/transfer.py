"""Background file copy/move that tolerates filesystems without Unix metadata.

MTP (phones) and many SMB/FAT mounts reject chmod/chown/utime with
EOPNOTSUPP. shutil.copy2 does copyfile + copystat, so on those targets every
copy raised OSError *after* the bytes had already transferred correctly.
Here metadata is best-effort: the data is what matters, and a target that
cannot store a mode bit is not a failed copy.
"""

import errno
import hashlib
import os
import shutil
import subprocess
from urllib.parse import quote

from PyQt6.QtCore import QThread, pyqtSignal


# errno values meaning "this filesystem has no such concept"
_METADATA_UNSUPPORTED = {
    errno.EOPNOTSUPP,   # == ENOTSUP on Linux
    errno.EPERM,
    errno.EACCES,
    errno.EINVAL,
}


def copy_metadata_best_effort(src: str, dst: str) -> None:
    """copystat, ignoring targets that cannot represent Unix metadata."""
    try:
        shutil.copystat(src, dst)
    except OSError as e:
        if e.errno not in _METADATA_UNSUPPORTED:
            raise


# shutil defaults to 64 KB on Linux, which on a fuse mount (MTP/SMB) means a
# syscall round trip every 64 KB. Measured writing 40 MB to an MTP phone:
# 64 KB -> 37.8 MB/s, 256 KB -> 78.2, 1 MB -> 120.9, 4 MB -> 117.7.
_COPY_BUFSIZE = 1024 * 1024


_GVFS_MTP_PREFIX = "mtp:host="


def mtp_uri(path: str) -> str | None:
    """The mtp:// URI for a path inside a gvfs MTP mount, else None.

    MTP is not a filesystem: the protocol moves whole files and has no
    concept of writing at an offset, so gvfsd-fuse answers every write()
    with EOPNOTSUPP no matter which flags the file was opened with. Reads
    work, which is why browsing a phone looks perfectly normal right up
    until the first copy onto it. Bytes can only go the other way through
    GIO's whole-file transfer, so a destination that resolves to a URI here
    has to be copied with `gio copy` instead of open()/write().
    """
    if not hasattr(os, "getuid"):
        return None
    gvfs_root = f"/run/user/{os.getuid()}/gvfs/"
    if not path.startswith(gvfs_root):
        return None
    mount, _, tail = path[len(gvfs_root):].partition("/")
    if not mount.startswith(_GVFS_MTP_PREFIX):
        return None    # SMB and the rest implement write() normally
    host = mount[len(_GVFS_MTP_PREFIX):]
    trail = "/" if tail.endswith("/") else ""
    body = "/".join(quote(part) for part in tail.strip("/").split("/") if part)
    return f"mtp://{host}/{body}{trail}"


def copy_file_gio(src: str, dst: str, uri: str, on_bytes=None) -> None:
    """Copy onto an MTP device with `gio copy`, the only write path it has.

    Progress lands in one step at the end of each file: `gio copy -p` reports
    only human-rounded, locale-formatted totals, and parsing those back into
    byte counts would be guesswork. Per-file granularity is honest instead.
    """
    try:
        result = subprocess.run(
            # No --force flag: gio copy already replaces an existing
            # destination, matching open(dst, "wb"), and older gio builds
            # reject -f outright. Keep this list to options gio has always had.
            ["gio", "copy", "--", src, uri],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise OSError("GIO is not installed, so files cannot be copied to the phone.")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "gio copy failed"
        raise OSError(detail)
    if on_bytes is not None:
        on_bytes(os.path.getsize(src))


def copy_file(src: str, dst: str, on_bytes=None, want_hash: bool = True) -> str:
    """Copy file contents, then metadata if the target supports it.

    Returns the SHA-256 of the source, hashed from the bytes as they stream
    past — free, since we are already reading them. `on_bytes` is called with
    each chunk length so a caller can report progress inside a large file.

    On an MTP destination the copy is handed to `gio copy` (see `mtp_uri`),
    which does its own reading; there the hash costs a second pass over the
    source, so it is only taken when the caller actually wants one.

    The size check is not paranoia: MTP and SMB report a short write by
    silently truncating rather than raising, so a phone that fills up
    mid-transfer otherwise looks like a clean copy.
    """
    expected = os.path.getsize(src)
    uri = mtp_uri(dst)
    if uri is not None:
        copy_file_gio(src, dst, uri, on_bytes=on_bytes)
        actual = os.path.getsize(dst)
        if actual != expected:
            raise OSError(
                f"incomplete copy: wrote {actual} of {expected} bytes "
                f"(destination may be full)"
            )
        copy_metadata_best_effort(src, dst)
        return hash_file(src) if want_hash else ""
    digest = hashlib.sha256()
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while chunk := fsrc.read(_COPY_BUFSIZE):
            fdst.write(chunk)
            digest.update(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
        fdst.flush()
    actual = os.path.getsize(dst)
    if actual != expected:
        raise OSError(
            f"incomplete copy: wrote {actual} of {expected} bytes "
            f"(destination may be full)"
        )
    copy_metadata_best_effort(src, dst)
    return digest.hexdigest()


def hash_file(path: str, on_bytes=None) -> str:
    """SHA-256 of `path`, read back in the same chunk size used for copying."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_COPY_BUFSIZE):
            digest.update(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
    return digest.hexdigest()


def measure(paths: list[str]) -> tuple[int, int]:
    """(total_bytes, file_count) for `paths`, recursing into directories.

    One walk producing both: sizing a tree on MTP/SMB is itself slow, so
    walking it twice doubled the wait before the progress bar could start.
    """
    total = 0
    count = 0
    for p in paths:
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        count += 1
                        try:
                            total += os.path.getsize(os.path.join(root, f))
                        except OSError:
                            pass
            else:
                count += 1
                total += os.path.getsize(p)
        except OSError:
            pass
    return total, count


class TransferWorker(QThread):
    """Runs a copy or move off the GUI thread.

    Every copy is size-checked, which is free and catches the common failure
    (MTP and SMB signal a short write by silently truncating).

    `verify=True` additionally reads each file back and compares its SHA-256
    against the hash taken while writing. That is the only real integrity
    guarantee on MTP, where fsync() and close() both return instantly and the
    filesystem offers no commit barrier — but it re-reads every byte over the
    wire, which roughly doubles transfer time. Measured on an MTP phone the
    hashing itself is only 1.3% of that; the cost is the readback. Off by
    default for that reason; turn it on when integrity matters more than speed.
    """

    # (copied_bytes, verified_bytes, current_name, files_done, files_total, phase)
    progress = pyqtSignal(object, object, str, int, int, str)
    totals = pyqtSignal(object, int)       # (total_bytes, total_files)
    failed = pyqtSignal(str, str)          # (path, error message)
    done = pyqtSignal(bool)                # True if cancelled

    PHASE_COPY = "copy"
    PHASE_VERIFY = "verify"

    def __init__(self, pairs: list[tuple[str, str]], move: bool, verify: bool = False):
        super().__init__()
        self._pairs = pairs                # [(src, dst), ...]
        self._move = move
        self._verify = verify
        self._cancel = False
        self._bytes = 0
        self._verified_bytes = 0
        self._files_done = 0
        self._files_total = 0
        self._total_bytes = 0
        self._phase = self.PHASE_COPY
        self._current = ""
        # (dst, expected_hash) queued during the copy, checked afterwards
        self._to_verify: list[tuple[str, str]] = []
        self._bad: set[str] = set()   # destinations that failed to verify

    def cancel(self) -> None:
        self._cancel = True

    def run(self):
        # Sizing walks the source tree, which is itself slow on MTP/SMB, so
        # it belongs here rather than on the GUI thread.
        sources = [src for src, _ in self._pairs]
        self._total_bytes, self._files_total = measure(sources)
        self.totals.emit(self._total_bytes, self._files_total)

        copied_pairs = []          # pairs whose source still needs removing
        for src, dst in self._pairs:
            if self._cancel:
                break
            try:
                if self._move and self._same_filesystem(src, dst):
                    os.rename(src, dst)          # instant, no data movement
                    self._bytes += self._size_of(src_after_move=dst)
                    self._emit_progress(src)     # source is already gone
                elif os.path.isdir(src) and not os.path.islink(src):
                    self._copy_tree(src, dst)
                    copied_pairs.append((src, dst))
                else:
                    self._copy_one(src, dst)
                    copied_pairs.append((src, dst))
            except OSError as e:
                self.failed.emit(src, str(e))

        self._verify_all()

        # A move deletes the original only once everything copied under it
        # has verified, so a failed verification can never lose data.
        if self._move and not self._cancel:
            for src, dst in copied_pairs:
                if self._verified_clean(dst):
                    self._remove_source(src)

        self.done.emit(self._cancel)

    def _verified_clean(self, dst: str) -> bool:
        """True if nothing at or beneath `dst` failed verification."""
        prefix = os.path.join(dst, "")
        return not any(bad == dst or bad.startswith(prefix) for bad in self._bad)

    def _remove_source(self, src: str) -> None:
        try:
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
        except OSError as e:
            self.failed.emit(src, str(e))

    # ── internals ─────────────────────────────────────────────────

    def _emit_progress(self, name_src: str) -> None:
        self._files_done += 1
        self._current = os.path.basename(name_src)
        self._emit()

    def _emit(self) -> None:
        self.progress.emit(
            self._bytes, self._verified_bytes, self._current,
            self._files_done, self._files_total, self._phase,
        )

    def _copy_one(self, src: str, dst: str) -> None:
        self._current = os.path.basename(src)
        # Report progress inside the file too, so a single large file does
        # not look frozen at one percentage for its whole transfer.
        def on_bytes(n):
            self._bytes += n
            self._emit()

        digest = copy_file(src, dst, on_bytes=on_bytes, want_hash=self._verify)
        if self._verify:
            self._to_verify.append((dst, digest))
        self._files_done += 1
        self._emit()

    def _verify_all(self) -> None:
        if not self._to_verify or self._cancel:
            return
        self._phase = self.PHASE_VERIFY
        self._files_done = 0
        self._files_total = len(self._to_verify)
        for dst, expected in self._to_verify:
            if self._cancel:
                return
            self._current = os.path.basename(dst)

            def on_bytes(n):
                self._verified_bytes += n
                self._emit()

            try:
                actual = hash_file(dst, on_bytes=on_bytes)
            except OSError as e:
                self._bad.add(dst)
                self.failed.emit(dst, f"could not read back to verify: {e}")
            else:
                if actual != expected:
                    self._bad.add(dst)
                    self.failed.emit(
                        dst, "verification FAILED — copy does not match source"
                    )
            self._files_done += 1
            self._emit()

    def _copy_tree(self, src: str, dst: str) -> None:
        os.makedirs(dst, exist_ok=True)
        try:
            entries = list(os.scandir(src))
        except OSError as e:
            self.failed.emit(src, str(e))
            return
        for entry in entries:
            if self._cancel:
                return
            target = os.path.join(dst, entry.name)
            try:
                if entry.is_dir(follow_symlinks=False):
                    self._copy_tree(entry.path, target)
                else:
                    self._copy_one(entry.path, target)
            except OSError as e:
                self.failed.emit(entry.path, str(e))
        copy_metadata_best_effort(src, dst)

    def _size_of(self, src_after_move: str) -> int:
        try:
            return os.path.getsize(src_after_move)
        except OSError:
            return 0

    @staticmethod
    def _same_filesystem(src: str, dst: str) -> bool:
        """True if a rename would work — same device, so no copy needed."""
        try:
            return os.stat(src).st_dev == os.stat(os.path.dirname(dst) or ".").st_dev
        except OSError:
            return False
