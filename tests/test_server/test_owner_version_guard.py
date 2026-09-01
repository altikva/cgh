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
    ipc.write_owner_version(tmp_path, "0.11.7")
    monkeypatch.setattr(ipc, "installed_cgh_version", lambda: "0.11.7")
    assert ipc.owner_version_current(tmp_path) is True


def test_owner_version_current_detects_drift(monkeypatch, tmp_path):
    ipc.write_owner_version(tmp_path, "0.11.6")
    monkeypatch.setattr(ipc, "installed_cgh_version", lambda: "0.11.7")
    assert ipc.owner_version_current(tmp_path) is False


def test_owner_version_current_fails_safe_when_unknown(monkeypatch, tmp_path):
    # No stamp on disk: cannot tell, so never force a restart.
    monkeypatch.setattr(ipc, "installed_cgh_version", lambda: "0.11.7")
    assert ipc.owner_version_current(tmp_path) is True

    # Stamp present but installed version unreadable: still reads as current.
    ipc.write_owner_version(tmp_path, "0.11.6")
    monkeypatch.setattr(ipc, "installed_cgh_version", lambda: None)
    assert ipc.owner_version_current(tmp_path) is True


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
