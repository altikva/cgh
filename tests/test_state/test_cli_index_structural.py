# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-30
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A foreground CLI index (cgh index, init, force-index) queues no
#              deferred LLM scan: one backend call per changed file turned a
#              seconds-long `init --from` into twenty minutes.

from __future__ import annotations

import argparse

import pytest

from codegraph.state import deferred_scan


@pytest.fixture(autouse=True)
def _reset_suspension():
    deferred_scan._SUSPENDED.clear()
    yield
    deferred_scan._SUSPENDED.clear()


def test_cli_index_queues_no_deferred_scan(tmp_path, monkeypatch):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    queued: list[str] = []
    monkeypatch.setattr(deferred_scan, "_process", lambda r, p, s: queued.append(p))

    def _fake_index_repo(root, **_):
        deferred_scan.enqueue(root, str(tmp_path / "mod.py"), "sha")
        return {"indexed": 1, "skipped": 0, "errors": 0, "elapsed_s": 0.0}

    monkeypatch.setattr("codegraph.indexer.index_repo", _fake_index_repo)

    from codegraph.cli.commands_index import cmd_index

    cmd_index(argparse.Namespace(root=str(tmp_path), verbose=False, method="auto"))
    deferred_scan.drain_for_tests()
    assert queued == []
    assert deferred_scan._SUSPENDED.is_set()
