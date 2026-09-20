# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: An owner records the cgh version it started under so a package
#              upgrade underneath a long-lived owner is detected. Covers the
#              version stamp round-trip, the fail-safe "unknown reads as
#              current" rule of owner_version_current, and stop_owner
#              terminating the owner and clearing its ipc files.

from __future__ import annotations

import pytest

from codegraph.state import ipc


def test_version_stamp_round_trip(tmp_path):
    assert ipc.read_owner_version(tmp_path) is None  # nothing stamped yet
    ipc.write_owner_version(tmp_path, "0.11.7")
    assert ipc.read_owner_version(tmp_path) == "0.11.7"


def test_write_owner_version_ignores_empty(tmp_path):
    ipc.write_owner_version(tmp_path, None)
    ipc.write_owner_version(tmp_path, "")
    assert not ipc.owner_version_file(tmp_path).exists()


def test_owner_version_current_matches(monkeypatch, tmp_path):
    ipc.write_owner_version(tmp_path, "0.11.7+abcdef0123456789")
    monkeypatch.setattr(
        ipc, "installed_cgh_fingerprint", lambda: "0.11.7+abcdef0123456789"
    )
    assert ipc.owner_version_current(tmp_path) is True


def test_owner_version_current_detects_drift(monkeypatch, tmp_path):
    ipc.write_owner_version(tmp_path, "0.11.6")
    monkeypatch.setattr(ipc, "installed_cgh_fingerprint", lambda: "0.11.7")
    assert ipc.owner_version_current(tmp_path) is False


def test_owner_version_current_detects_same_version_code_swap(monkeypatch, tmp_path):
    # The bug this guards against: the version string is unchanged (a
    # develop-to-develop reinstall), but the RECORD hash folded into the
    # fingerprint differs, so the stale owner is correctly retired.
    ipc.write_owner_version(tmp_path, "0.13.0+1111111111111111")
    monkeypatch.setattr(
        ipc, "installed_cgh_fingerprint", lambda: "0.13.0+2222222222222222"
    )
    assert ipc.owner_version_current(tmp_path) is False


def test_owner_version_current_fails_safe_when_unknown(monkeypatch, tmp_path):
    # No stamp on disk: cannot tell, so never force a restart.
    monkeypatch.setattr(ipc, "installed_cgh_fingerprint", lambda: "0.11.7")
    assert ipc.owner_version_current(tmp_path) is True

    # Stamp present but installed fingerprint unreadable: still reads as current.
    ipc.write_owner_version(tmp_path, "0.11.6")
    monkeypatch.setattr(ipc, "installed_cgh_fingerprint", lambda: None)
    assert ipc.owner_version_current(tmp_path) is True


def test_fingerprint_is_version_prefixed(monkeypatch):
    # Whatever the install shape, the fingerprint starts with the version so a
    # version bump is still visible, and it never raises. A hashed RECORD (a
    # wheel or tool install) adds a "+<hash>" suffix; an editable/from-source
    # install with no hashed RECORD falls back to the bare version.
    monkeypatch.setattr(ipc, "installed_cgh_version", lambda: "9.9.9")
    fp = ipc.installed_cgh_fingerprint()
    assert fp is not None and fp.startswith("9.9.9")
    if "+" in fp:
        assert len(fp.split("+", 1)[1]) == 16


# A wheel/tool RECORD lists the source files with hashes; an editable (PEP 660)
# RECORD is hashed but lists only the .pth shim, so its hash is blind to source
# edits and must be refused.
_WHEEL_RECORD = (
    "codegraph/__init__.py,sha256=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,120\n"
    "codegraph/state/ipc.py,sha256=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB,4200\n"
    "cgh-0.13.0.dist-info/RECORD,,\n"
)
_EDITABLE_RECORD = (
    "__editable__.cgh-0.13.0.pth,sha256=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC,42\n"
    "__editable___cgh_0_13_0_finder.py,sha256=DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD,900\n"
    "cgh-0.13.0.dist-info/RECORD,,\n"
)


def test_record_fingerprint_hashes_a_wheel_record():
    fp = ipc._record_fingerprint(_WHEEL_RECORD, "codegraph/__init__.py", "0.13.0")
    assert fp is not None and fp.startswith("0.13.0+")
    assert len(fp.split("+", 1)[1]) == 16


def test_record_fingerprint_moves_when_source_changes():
    # A different source hash in RECORD yields a different fingerprint, which is
    # the whole point: a develop-to-develop swap is caught.
    changed = _WHEEL_RECORD.replace("AAAAAAA", "ZZZZZZZ")
    a = ipc._record_fingerprint(_WHEEL_RECORD, "codegraph/__init__.py", "0.13.0")
    b = ipc._record_fingerprint(changed, "codegraph/__init__.py", "0.13.0")
    assert a is not None and b is not None and a != b


def test_record_fingerprint_refuses_editable_record():
    # Hashed, but lists no source file: the hash can't reflect code edits, so it
    # must fall back to the bare version rather than a precise-looking constant.
    assert (
        ipc._record_fingerprint(_EDITABLE_RECORD, "codegraph/__init__.py", "0.13.0")
        is None
    )


def test_record_fingerprint_refuses_unhashed_record():
    assert (
        ipc._record_fingerprint(
            "codegraph/__init__.py,,\n", "codegraph/__init__.py", "0.13.0"
        )
        is None
    )


def test_stop_owner_terminates_and_clears_files(monkeypatch, tmp_path):
    root = tmp_path
    (root / ".codegraph").mkdir()
    ipc.owner_pidfile(root).write_text("4242\n", encoding="utf-8")
    ipc.port_file(root).write_text("5555\n", encoding="utf-8")
    ipc.write_owner_version(root, "0.11.6")

    killed: list[int] = []
    monkeypatch.setattr(
        "codegraph.state.pidfile.terminate",
        lambda pid, graceful_timeout=5.0: killed.append(pid),
    )

    assert ipc.stop_owner(root) is True
    assert killed == [4242]
    assert not ipc.owner_pidfile(root).exists()
    assert not ipc.port_file(root).exists()
    assert not ipc.owner_version_file(root).exists()


def test_stop_owner_noop_without_pid(tmp_path):
    (tmp_path / ".codegraph").mkdir()
    assert ipc.stop_owner(tmp_path) is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
