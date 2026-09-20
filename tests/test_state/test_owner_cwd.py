# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-21
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: spawn_owner must start the owner process in its own repo (cwd == --root),
#              so an owner can never open another worktree's call_log.db.

from __future__ import annotations

from pathlib import Path

from codegraph.state import ipc


def test_spawn_owner_pins_cwd_to_root(monkeypatch, tmp_path):
    (tmp_path / ".codegraph").mkdir()
    captured = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            captured["cwd"] = kw.get("cwd")

    monkeypatch.setattr(ipc.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ipc, "_await_owner_port", lambda repo_root, timeout: 4242)
    port = ipc.spawn_owner(tmp_path, watch=False, reindex=False)
    assert port == 4242
    assert captured["cwd"] is not None
    assert Path(captured["cwd"]).resolve() == tmp_path.resolve()
