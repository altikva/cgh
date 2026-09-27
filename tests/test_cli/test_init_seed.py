# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-27
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh init --from` owner handling: a live owner on the source still
#              aborts (other sessions may rely on it), a live owner on the target
#              is stopped and the seed goes ahead.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.cli import commands_init
from codegraph.state import ipc, relocate


def _checkouts(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path.resolve()
    src, dst = base / "main", base / "wt"
    (src / ".codegraph").mkdir(parents=True)
    (dst / ".codegraph").mkdir(parents=True)
    return src, dst


def _fake_relocate(from_root, to_root):
    return {"copied": ["graph.duckdb"], "rewritten_rows": 0}


def test_target_owner_is_stopped_then_seeded(tmp_path, monkeypatch):
    src, dst = _checkouts(tmp_path)
    alive = {dst}
    stopped: list[Path] = []

    def fake_stop(root, graceful_timeout=5.0):
        stopped.append(Path(root))
        alive.discard(Path(root))
        return True

    monkeypatch.setattr(ipc, "is_owner_alive", lambda r: Path(r) in alive)
    monkeypatch.setattr(ipc, "read_owner_pid", lambda r: 4242)
    monkeypatch.setattr(ipc, "is_pid_alive", lambda pid: False)
    monkeypatch.setattr(ipc, "stop_owner", fake_stop)
    monkeypatch.setattr(relocate, "relocate_store", _fake_relocate)

    assert commands_init._seed_from_checkout(dst, src) is True
    assert stopped == [dst]


def test_target_owner_that_survives_the_stop_aborts(tmp_path, monkeypatch):
    src, dst = _checkouts(tmp_path)
    monkeypatch.setattr(ipc, "is_owner_alive", lambda r: Path(r) == dst)
    monkeypatch.setattr(ipc, "read_owner_pid", lambda r: 4242)
    monkeypatch.setattr(ipc, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr(ipc, "stop_owner", lambda root, graceful_timeout=5.0: True)
    monkeypatch.setattr(relocate, "relocate_store", _fake_relocate)

    with pytest.raises(SystemExit):
        commands_init._seed_from_checkout(dst, src)


def test_source_owner_still_aborts(tmp_path, monkeypatch):
    src, dst = _checkouts(tmp_path)
    stopped: list[Path] = []
    monkeypatch.setattr(ipc, "is_owner_alive", lambda r: Path(r) == src)
    monkeypatch.setattr(ipc, "stop_owner", lambda root, **_: stopped.append(root))
    monkeypatch.setattr(relocate, "relocate_store", _fake_relocate)

    with pytest.raises(SystemExit):
        commands_init._seed_from_checkout(dst, src)
    assert stopped == []
