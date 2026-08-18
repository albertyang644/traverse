"""Copying onto MTP: destination detection and the gio-copy fallback.

MTP answers write() with EOPNOTSUPP, so these copies must never go through
open()/write(). The tests pin the routing decision, not the phone.
"""

import os
import shutil
import subprocess

import pytest

from src import transfer


@pytest.fixture
def gvfs_root(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    return "/run/user/1000/gvfs"


def test_mtp_path_maps_to_uri(gvfs_root):
    path = f"{gvfs_root}/mtp:host=SAMSUNG_R52T/Internal storage/Movies/clip.mp4"
    assert transfer.mtp_uri(path) == (
        "mtp://SAMSUNG_R52T/Internal%20storage/Movies/clip.mp4"
    )


def test_uri_escapes_characters_that_break_a_url(gvfs_root):
    path = f"{gvfs_root}/mtp:host=PHONE/Internal storage/Pilates (1)/a&b#c.srt"
    uri = transfer.mtp_uri(path)
    assert uri == "mtp://PHONE/Internal%20storage/Pilates%20%281%29/a%26b%23c.srt"


def test_local_path_is_not_mtp(gvfs_root):
    assert transfer.mtp_uri("/home/user/Downloads/clip.mp4") is None


def test_smb_mount_is_not_mtp(gvfs_root):
    # SMB implements write() normally; routing it through gio would be a
    # pointless slowdown and would lose the streaming hash.
    assert transfer.mtp_uri(f"{gvfs_root}/smb-share:server=nas,share=media/x") is None


def test_mtp_copy_uses_gio_not_write(gvfs_root, tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"payload" * 100)
    dst = f"{gvfs_root}/mtp:host=PHONE/Internal storage/clip.mp4"
    calls = []

    class Result:
        returncode = 0
        stdout = stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(transfer.subprocess, "run", fake_run)
    monkeypatch.setattr(transfer.os.path, "getsize",
                        lambda p: len(b"payload" * 100))
    monkeypatch.setattr(transfer, "copy_metadata_best_effort", lambda s, d: None)

    digest = transfer.copy_file(str(src), dst, want_hash=True)
    assert calls == [["gio", "copy", "--", str(src), transfer.mtp_uri(dst)]]
    assert digest == transfer.hash_file(str(src))


def test_mtp_copy_skips_hashing_when_not_verifying(gvfs_root, tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"payload")
    dst = f"{gvfs_root}/mtp:host=PHONE/Internal storage/clip.mp4"

    class Result:
        returncode = 0
        stdout = stderr = ""

    monkeypatch.setattr(transfer.subprocess, "run", lambda cmd, **kw: Result())
    monkeypatch.setattr(transfer.os.path, "getsize", lambda p: 7)
    monkeypatch.setattr(transfer, "copy_metadata_best_effort", lambda s, d: None)
    assert transfer.copy_file(str(src), dst, want_hash=False) == ""


def test_gio_failure_surfaces_as_oserror(gvfs_root, tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"payload")
    dst = f"{gvfs_root}/mtp:host=PHONE/Internal storage/clip.mp4"

    class Result:
        returncode = 1
        stdout = ""
        stderr = "gio: No space left on device"

    monkeypatch.setattr(transfer.subprocess, "run", lambda cmd, **kw: Result())
    with pytest.raises(OSError, match="No space left"):
        transfer.copy_file(str(src), dst)


def test_short_copy_is_still_caught_on_mtp(gvfs_root, tmp_path, monkeypatch):
    """A phone that fills up truncates silently; the size check must catch it."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"payload" * 100)
    dst = f"{gvfs_root}/mtp:host=PHONE/Internal storage/clip.mp4"

    class Result:
        returncode = 0
        stdout = stderr = ""

    sizes = {str(src): 700, dst: 120}
    monkeypatch.setattr(transfer.subprocess, "run", lambda cmd, **kw: Result())
    monkeypatch.setattr(transfer.os.path, "getsize", lambda p: sizes[p])
    with pytest.raises(OSError, match="incomplete copy"):
        transfer.copy_file(str(src), dst)


def test_gio_accepts_the_options_we_pass(tmp_path):
    """Run the real gio with our exact option list, against local files.

    The mocked tests above cannot catch an option the installed gio does not
    have: a bad flag still "passes" against a fake subprocess. Shipping -f,
    which older builds reject, is exactly the failure that got through. Local
    paths keep this hermetic — it checks the invocation, not the phone.
    """
    gio = shutil.which("gio")
    if gio is None:
        pytest.skip("gio not installed")
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"payload")
    cmd = ["gio", "copy", "--", str(src), str(dst)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert dst.read_bytes() == b"payload"
    # And it must replace an existing destination, as open(dst, "wb") does.
    src.write_bytes(b"second")
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert dst.read_bytes() == b"second"


def test_local_copy_still_streams(tmp_path):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"local payload")
    digest = transfer.copy_file(str(src), str(dst))
    assert dst.read_bytes() == b"local payload"
    assert digest == transfer.hash_file(str(src))
