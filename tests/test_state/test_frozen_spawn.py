# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: spawn_owner adapts its launch command to a frozen binary. A
#              normal install spawns "python -m codegraph _serve_owner"; a
#              PyInstaller/Nuitka binary (sys.frozen set) has no interpreter to
#              run -m against, so it invokes the _serve_owner subcommand on the
#              binary itself.

from __future__ import annotations

import sys

import pytest

from codegraph.state import ipc


@pytest.fixture
def capture_spawn(monkeypatch):
    """Capture the argv spawn_owner would launch, without starting anything."""
    captured: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd

    monkeypatch.setattr(ipc.subprocess, "Popen", _FakePopen)
    # Report the owner as up immediately so spawn_owner returns without waiting.
    monkeypatch.setattr(ipc, "is_owner_alive", lambda root: True)
    monkeypatch.setattr(ipc, "read_owner_port", lambda root: 4321)
    monkeypatch.setattr(ipc, "rotate_owner_log", lambda root: None)
    monkeypatch.setattr(sys, "executable", "/opt/cgh/bin/cgh", raising=False)
    return captured


def test_normal_install_spawns_via_dash_m(capture_spawn, tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    ipc.spawn_owner(tmp_path, watch=False, reindex=False)
    cmd = capture_spawn["cmd"]
    assert cmd[:4] == ["/opt/cgh/bin/cgh", "-m", "codegraph", "_serve_owner"]


def test_frozen_binary_spawns_the_subcommand_directly(
    capture_spawn, tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    ipc.spawn_owner(tmp_path, watch=True, reindex=False)
    cmd = capture_spawn["cmd"]
    # No "-m codegraph": the binary routes its own argv through the CLI.
    assert cmd[0] == "/opt/cgh/bin/cgh"
    assert cmd[1] == "_serve_owner"
    assert "-m" not in cmd and "codegraph" not in cmd
    assert "--watch" in cmd  # flags still appended


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
