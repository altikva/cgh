# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The git-hook reindex stays structural (no deferred LLM
#              scanner), a [plugins] disable written after the process
#              started still stops a deferred scanner, and force-index
#              runs at all (it imported a name the indexer dropped).

from __future__ import annotations

import argparse
import os

import pytest

from codegraph.state import deferred_scan


@pytest.fixture(autouse=True)
def _reset_deferred_state():
    deferred_scan._SUSPENDED.clear()
    deferred_scan._PLUGIN_CFG.clear()
    yield
    deferred_scan._SUSPENDED.clear()
    deferred_scan._PLUGIN_CFG.clear()


def test_hook_reindex_queues_no_deferred_scan(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    queued: list[str] = []

    def _fake_index_repo(root, method="incremental"):
        deferred_scan.enqueue(root, os.path.join(root, "a.py"), "sha")

    monkeypatch.setattr("codegraph.indexer.index_repo", _fake_index_repo)
    monkeypatch.setattr(deferred_scan, "_process", lambda r, p, s: queued.append(p))

    from codegraph.cli.commands_index import cmd_reindex_hook

    cmd_reindex_hook(argparse.Namespace(root=str(tmp_path)))
    deferred_scan.drain_for_tests()
    assert queued == []


def test_disable_written_after_start_is_honoured(tmp_path):
    cfg = tmp_path / ".codegraph" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text("[codegraph]\n")
    root = str(tmp_path)
    assert deferred_scan._plugin_allowed(root, "summarize")

    cfg.write_text('[codegraph]\n\n[plugins]\ndisabled = ["summarize"]\n')
    st = cfg.stat()
    os.utime(cfg, (st.st_atime, st.st_mtime + 5))
    assert not deferred_scan._plugin_allowed(root, "summarize")
    assert deferred_scan._plugin_allowed(root, "pii")


def test_force_index_walks_parseable_files(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("def f():\n    return 1\n")
    (pkg / "blob.unknownext").write_text("noise")
    seen: list[str] = []

    def _fake_index_file(path, repo_root=None, force=False, **_):
        seen.append(os.path.basename(str(path)))
        return True

    monkeypatch.setattr("codegraph.indexer.index_file", _fake_index_file)

    from codegraph.cli.commands_index import cmd_force_index

    cmd_force_index(argparse.Namespace(root=str(tmp_path), paths=["pkg"], yes=True))
    assert seen == ["mod.py"]
