"""A malicious tar must not write outside the extraction directory."""

import os
import tarfile

from src.file_pane import FilePaneWidget


def _evil_tar(path, member_name):
    payload = path.parent / "payload"
    payload.write_text("pwned")
    with tarfile.open(path, "w") as tar:
        tar.add(payload, arcname=member_name)
    payload.unlink()


def test_parent_traversal_member_is_refused(tmp_path):
    archive = tmp_path / "evil.tar"
    dest = tmp_path / "dest"
    dest.mkdir()
    _evil_tar(archive, "../escaped.txt")

    try:
        FilePaneWidget._unpack(str(archive), str(dest))
    except (tarfile.TarError, OSError, ValueError):
        pass                                    # refused outright — fine

    assert not (tmp_path / "escaped.txt").exists()
    assert not os.path.exists("/tmp/escaped.txt")


def test_ordinary_member_still_extracts(tmp_path):
    archive = tmp_path / "good.tar"
    dest = tmp_path / "dest"
    dest.mkdir()
    _evil_tar(archive, "inner/file.txt")

    FilePaneWidget._unpack(str(archive), str(dest))
    assert (dest / "inner" / "file.txt").read_text() == "pwned"
