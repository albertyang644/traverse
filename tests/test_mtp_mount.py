"""MTP connect recovery: the device node parse, the holder scan, and the
guard that decides whether a holder may be evicted."""

import os

from src.tree_panel import TreePanel, _MTP_BACKENDS


def _may_evict(holders):
    return bool(holders) and all(comm in _MTP_BACKENDS for _, comm in holders)


def test_device_node_parsed_from_gio_error():
    detail = 'gio: mtp://SAMSUNG_R52T/: Unable to open MTP device “002,017”'
    assert TreePanel._mtp_device_node(detail) == "/dev/bus/usb/002/017"


def test_device_node_zero_padded():
    assert TreePanel._mtp_device_node('Unable to open MTP device "2,7"') == "/dev/bus/usb/002/007"


def test_device_node_absent_for_unrelated_error():
    assert TreePanel._mtp_device_node("Device is locked") is None


def test_backend_holders_may_be_evicted():
    assert _may_evict([(1, "kiod5"), (2, "gvfsd-mtp")])


def test_real_application_is_never_evicted():
    assert not _may_evict([(1, "kiod5"), (2, "dolphin")])
    assert not _may_evict([])


def test_holder_scan_finds_this_process(tmp_path):
    target = tmp_path / "fake-usb-node"
    target.write_bytes(b"")
    with open(target) as handle:  # noqa: F841 - fd must stay open during the scan
        pids = [pid for pid, _ in TreePanel._usb_device_holders(str(target))]
    assert os.getpid() in pids


def test_holder_scan_empty_when_nothing_holds_node(tmp_path):
    assert TreePanel._usb_device_holders(str(tmp_path / "unopened")) == []


def test_failure_message_names_the_blocking_program():
    message = TreePanel._mount_failure_message("boom", [(999, "dolphin")])
    assert "dolphin (pid 999)" in message
    assert "GIO: boom" in message


def test_failure_message_falls_back_to_generic_advice():
    message = TreePanel._mount_failure_message("boom", [])
    assert "File Transfer / MTP" in message
