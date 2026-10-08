# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: An index written in an older graph format answers with fewer
#              edges until its one-time re-parse. `cgh status` (human and
#              --json) must say so instead of "fresh", and say "reindexing"
#              while the re-parse runs; `cgh doctor` shows it as a
#              non-blocking `!!` line; `init --from` warns before seeding
#              from such a source. Also covers `cgh callers` grouping the
#              candidate definitions once per distinct list.

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from rich.console import Console

import codegraph.cli.commands_query as cq
import codegraph.cli.owner_client as oc
from codegraph.cli import commands_monitor
from codegraph.cli.owner_client import OwnerReply
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.state.scan_meta import GRAPH_FORMAT, scan_status


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / ".gitignore").write_text(".codegraph/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "a")
    reset_connection()
    index_repo(root)
    reset_connection()
    yield root
    reset_connection()


def _set_format(root: Path, fmt: int | None) -> None:
    path = root / ".codegraph" / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    if fmt is None:
        meta.pop("graph_format", None)
    else:
        meta["graph_format"] = fmt
    path.write_text(json.dumps(meta), encoding="utf-8")


def _status_json(root: Path, capsys) -> dict:
    commands_monitor.cmd_status(
        argparse.Namespace(root=str(root), json=True, refresh=False, workers=False)
    )
    return json.loads(capsys.readouterr().out)["scan"]


def test_current_index_is_fresh(repo, capsys):
    scan = _status_json(repo, capsys)
    assert scan["fresh"] is True and scan["state"] == "fresh"
    assert scan["format_outdated"] is False
    assert scan["graph_format"] == scan["graph_format_current"] == GRAPH_FORMAT


@pytest.mark.parametrize("fmt", [GRAPH_FORMAT - 1, None])
def test_older_format_reads_outdated(repo, capsys, fmt):
    _set_format(repo, fmt)
    scan = _status_json(repo, capsys)
    assert scan["fresh"] is False and scan["state"] == "outdated"
    assert scan["format_outdated"] is True
    assert scan["graph_format"] == (fmt if fmt is not None else 1)


def test_reparse_running_reads_reindexing(repo, capsys):
    from codegraph.state import index_lock

    _set_format(repo, GRAPH_FORMAT - 1)
    index_lock._lock_path(repo).write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        ss = scan_status(repo)
        assert ss["state"] == "reindexing" and ss["indexing"]["pid"] == os.getpid()
        line = commands_monitor._scan_line({}, ss)
        assert line.startswith("[yellow]reindexing[/yellow]")
        assert "re-parse running" in line
    finally:
        index_lock._lock_path(repo).unlink()


def test_human_status_says_outdated(repo, capsys, monkeypatch):
    _set_format(repo, GRAPH_FORMAT - 1)
    monkeypatch.setattr(commands_monitor.console, "width", 300)
    commands_monitor.cmd_status(
        argparse.Namespace(root=str(repo), json=False, refresh=False, workers=False)
    )
    scan_row = next(
        ln
        for ln in capsys.readouterr().out.splitlines()
        if ln.strip().startswith("Scan")
    )
    assert "outdated" in scan_row and "fresh" not in scan_row
    assert "cgh index" in scan_row


def _doctor(repo, monkeypatch, capsys, strict: bool) -> str:
    monkeypatch.setattr(commands_monitor.console, "width", 300)
    commands_monitor.cmd_doctor(argparse.Namespace(root=str(repo), strict=strict))
    return capsys.readouterr().out


def test_doctor_flags_outdated_index_without_blocking(repo, monkeypatch, capsys):
    assert "graph format" not in _doctor(repo, monkeypatch, capsys, strict=False)
    (repo / ".codegraph" / "config.toml").write_text("[codegraph]\n")
    _set_format(repo, GRAPH_FORMAT - 1)
    out = _doctor(repo, monkeypatch, capsys, strict=True)  # must not raise
    line = next(ln for ln in out.splitlines() if "graph format" in ln)
    assert line.startswith("!!")
    assert f"format {GRAPH_FORMAT - 1}" in line and f"needs {GRAPH_FORMAT}" in line


def test_init_from_outdated_source_warns_before_seeding(tmp_path, repo, monkeypatch):
    from codegraph.cli import commands_init
    from codegraph.state import relocate

    _set_format(repo, GRAPH_FORMAT - 1)
    target = tmp_path / "target"
    (target / ".codegraph").mkdir(parents=True)
    buf = io.StringIO()
    monkeypatch.setattr(commands_init, "console", Console(file=buf, width=300))

    def _no_copy(*_a, **_k):
        raise relocate.RelocateError("stopped by the test")

    monkeypatch.setattr(relocate, "relocate_store", _no_copy)
    assert commands_init._seed_from_checkout(target, repo) is False
    out = buf.getvalue()
    assert f"graph format {GRAPH_FORMAT - 1}" in out
    assert f"cgh index --root {repo}" in out

    buf.truncate(0)
    _set_format(repo, GRAPH_FORMAT)
    commands_init._seed_from_checkout(target, repo)
    assert "graph format" not in buf.getvalue()


def test_callers_group_candidates_once(tmp_path, monkeypatch):
    targets = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]
    data = {
        "fn": "publish",
        "callers": [
            {
                "caller": f"c{i}",
                "file": str(tmp_path / f"c{i}.py"),
                "line": i,
                "targets": targets,
            }
            for i in range(5)
        ]
        + [
            {
                "caller": "solo",
                "file": str(tmp_path / "s.py"),
                "line": 9,
                "targets": [targets[0]],
            }
        ],
    }
    monkeypatch.setattr(
        oc, "call_owner_tool", lambda *a, **k: OwnerReply("ok", data=data)
    )
    buf = io.StringIO()
    monkeypatch.setattr(cq, "console", Console(file=buf, width=200, no_color=True))
    cq.cmd_callers(argparse.Namespace(root=str(tmp_path), fn_name="publish"))
    out = buf.getvalue()
    assert out.count("-> a.py, b.py") == 1 and "(5 callers)" in out
    assert out.count("-> a.py  (1 caller)") == 1
    for i in range(5):
        assert f"c{i}  c{i}.py:{i}" in out
    assert "solo  s.py:9" in out
