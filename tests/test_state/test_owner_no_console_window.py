# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-03
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: On Windows the owner must launch with CREATE_NO_WINDOW, not
#              DETACHED_PROCESS. A windowless parent (MCP client, or a federation
#              parent spawning one owner per child) has no console to detach
#              from, so DETACHED_PROCESS allocates a fresh console per owner and
#              a console window flashes open for every repo. These Win32 flags
#              exist only on Windows, so the test fakes os.name and the
#              subprocess constants to exercise the branch on any platform.

from __future__ import annotations

import os as _os

import pytest

from codegraph.state import ipc


class _WindowsOS:
    """Delegates to the real os but reports name == 'nt'.

    Patching os.name on the global module would make pathlib build a
    WindowsPath and fail on a POSIX host; swapping only ipc's os reference
    flips the branch while pathlib keeps using the real platform.
    """

    name = "nt"

    def __getattr__(self, attr):
        return getattr(_os, attr)


# Real Win32 process-creation flag values (constants absent off Windows).
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008


@pytest.fixture
def capture_windows_spawn(monkeypatch):
    """Capture the Popen kwargs spawn_owner would use, pretending to be Windows."""
    captured: dict = {}

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured["kwargs"] = kwargs

    monkeypatch.setattr(ipc.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(ipc, "rotate_owner_log", lambda root: None)
    monkeypatch.setattr(ipc, "_await_owner_port", lambda root, timeout: 4321)
    monkeypatch.setattr(ipc, "os", _WindowsOS())
    monkeypatch.setattr(
        ipc.subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW, raising=False
    )
    monkeypatch.setattr(
        ipc.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        _CREATE_NEW_PROCESS_GROUP,
        raising=False,
    )
    monkeypatch.setattr(
        ipc.subprocess, "DETACHED_PROCESS", _DETACHED_PROCESS, raising=False
    )
    return captured


def test_windows_owner_launches_without_a_console(capture_windows_spawn, tmp_path):
    ipc.spawn_owner(tmp_path, watch=False, reindex=False)
    flags = capture_windows_spawn["kwargs"]["creationflags"]

    assert flags & _CREATE_NO_WINDOW, "owner must run with no console window"
    assert flags & _CREATE_NEW_PROCESS_GROUP, "owner must leave the parent's group"
    # The regression: DETACHED_PROCESS allocates a fresh console per owner.
    assert not (flags & _DETACHED_PROCESS), "owner must not allocate its own console"
    # Windows never uses the POSIX session flag.
    assert "start_new_session" not in capture_windows_spawn["kwargs"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
