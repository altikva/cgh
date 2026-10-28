# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: On-demand indexing in the Read and Grep hooks: a repo file the
#              index lacks is indexed by the live owner (mocked here) or
#              parsed in memory without one, a file outside the repo is only
#              parsed in memory, a gitignored file is left alone, and the
#              step gives up past its budget.

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import subprocess
import time

import pytest

from codegraph.analysis import on_demand
from codegraph.cli import commands_hooks as h
from codegraph.cli import owner_client
from codegraph.cli.owner_client import OwnerReply


def _source(n: int = 6, pad: int = 0) -> str:
    """A module with ``n`` functions, padded with comment lines."""
    body = "".join(f"def handler_{i}(x):\n    return x + {i}\n\n\n" for i in range(n))
    return body + "".join(f"# padding line {i} of the module\n" for i in range(pad))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo with an empty FTS index and answer mode on, no owner."""
    root = tmp_path / "repo"
    (root / ".codegraph").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("gen/\n")
    conn = sqlite3.connect(root / ".codegraph" / "fts.db")
    conn.execute(
        "CREATE TABLE symbols (sym_id TEXT PRIMARY KEY, kind TEXT, name TEXT, "
        "file_path TEXT, start_line INTEGER, end_line INTEGER, docstring TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CGH_HOOK_ANSWERS", "1")
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", tmp_path / "home-cg")
    monkeypatch.setattr(owner_client, "live_owner_port", lambda root: None)
    return root


def _fts_rows(root) -> int:
    conn = sqlite3.connect(root / ".codegraph" / "fts.db")
    try:
        return conn.execute("SELECT count(*) FROM symbols").fetchone()[0]
    finally:
        conn.close()


def _hook(monkeypatch, capsys, func, tool_input: dict, cwd) -> dict:
    payload = {"tool_input": tool_input, "cwd": str(cwd), "session_id": "s-1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as exc:
        func(argparse.Namespace())
    assert exc.value.code == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out else {}


def _read(monkeypatch, capsys, path, cwd) -> dict:
    return _hook(
        monkeypatch, capsys, h.cmd_hook_precheck_read, {"file_path": str(path)}, cwd
    )


class _FakeOwner:
    """A live owner whose file_summary indexes the file into the FTS."""

    def __init__(self, root, status: str = "indexed") -> None:
        self.root = root
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, root, tool, arguments=None, *, timeout=None, port=None):
        self.calls.append((tool, arguments or {}))
        if self.status == "indexed":
            path = self.root / arguments["file_path"]
            conn = sqlite3.connect(self.root / ".codegraph" / "fts.db")
            for i in range(6):
                conn.execute(
                    "INSERT INTO symbols VALUES (?, 'function', ?, ?, ?, ?, '')",
                    (f"{path}::{i}", f"handler_{i}", str(path), 1 + 4 * i, 2 + 4 * i),
                )
            conn.commit()
            conn.close()
        if self.status == "timeout":
            return OwnerReply("timeout", error="slow")
        return OwnerReply("ok", data={"found": True, "on_demand": self.status})


def _with_owner(monkeypatch, owner: _FakeOwner) -> _FakeOwner:
    monkeypatch.setattr(owner_client, "live_owner_port", lambda root: 4321)
    monkeypatch.setattr(owner_client, "call_owner_tool", owner)
    return owner


def test_new_file_without_owner_is_parsed_in_memory(monkeypatch, capsys, repo):
    src = repo / "app.py"
    src.write_text(_source())
    ctx = _read(monkeypatch, capsys, src, repo)["additionalContext"]
    assert "Outline of app.py parsed just now, not in the index" in ctx
    assert "1-2  function  handler_0" in ctx
    assert _fts_rows(repo) == 0  # nothing written


def test_large_new_file_is_denied_with_its_parsed_outline(monkeypatch, capsys, repo):
    src = repo / "big.py"
    src.write_text(_source(pad=1200))
    assert src.stat().st_size > 32 * 1024
    out = _read(monkeypatch, capsys, src, repo)
    assert out["permissionDecision"] == "deny"
    assert "function  handler_5" in out["permissionDecisionReason"]


def test_new_file_with_owner_is_indexed_then_answered(monkeypatch, capsys, repo):
    owner = _with_owner(monkeypatch, _FakeOwner(repo))
    src = repo / "app.py"
    src.write_text(_source())
    ctx = _read(monkeypatch, capsys, src, repo)["additionalContext"]
    assert owner.calls == [("file_summary", {"file_path": "app.py"})]
    assert "Outline of app.py from the code graph (indexed just now)" in ctx
    assert _fts_rows(repo) == 6


def test_fresh_indexed_file_does_not_call_the_owner(monkeypatch, capsys, repo):
    src = repo / "app.py"
    src.write_text(_source())
    owner = _FakeOwner(repo)
    owner(None, "file_summary", {"file_path": "app.py"})  # the index has it
    owner.calls.clear()
    _with_owner(monkeypatch, owner)
    ctx = _read(monkeypatch, capsys, src, repo)["additionalContext"]
    assert owner.calls == []
    assert "Outline of app.py from the code graph:" in ctx


def test_file_outside_the_repo_is_parsed_and_never_written(
    monkeypatch, capsys, repo, tmp_path
):
    owner = _with_owner(monkeypatch, _FakeOwner(repo))
    scratch = tmp_path / "scratch" / "notes_tool.py"
    scratch.parent.mkdir()
    scratch.write_text(_source())
    ctx = _read(monkeypatch, capsys, scratch, repo)["additionalContext"]
    assert "parsed just now, not in the index" in ctx
    assert "handler_3" in ctx
    assert owner.calls == []
    assert _fts_rows(repo) == 0
    assert not (repo / ".codegraph" / "graph.duckdb").exists()


def test_gitignored_file_is_left_alone(monkeypatch, capsys, repo):
    src = repo / "gen" / "client.py"
    src.parent.mkdir()
    src.write_text(_source())
    assert _read(monkeypatch, capsys, src, repo) == {}
    assert on_demand.classify(src, repo) == "ignored"


def test_gitignored_file_with_owner_is_left_alone(monkeypatch, capsys, repo):
    _with_owner(monkeypatch, _FakeOwner(repo, status="ignored"))
    src = repo / "gen" / "client.py"
    src.parent.mkdir()
    src.write_text(_source())
    assert _read(monkeypatch, capsys, src, repo) == {}
    assert _fts_rows(repo) == 0


def test_slow_parse_falls_back_within_the_budget(monkeypatch, capsys, repo):
    monkeypatch.setenv("CGH_ON_DEMAND_BUDGET_MS", "100")

    class _Slow:
        def parse(self, path):
            time.sleep(2)

    monkeypatch.setattr("codegraph.parsers.get_parser_for_path", lambda p: _Slow())
    src = repo / "app.py"
    src.write_text(_source())
    start = time.monotonic()
    out = _read(monkeypatch, capsys, src, repo)
    assert time.monotonic() - start < 1.0
    assert out == {}  # today's answer for a small file the index lacks


def test_owner_timeout_falls_back(monkeypatch, capsys, repo):
    _with_owner(monkeypatch, _FakeOwner(repo, status="timeout"))
    src = repo / "big.py"
    src.write_text(_source(pad=1200))
    reason = _read(monkeypatch, capsys, src, repo)["permissionDecisionReason"]
    # The plan of a file cgh knows nothing about: no symbol in it.
    assert "handler_" not in reason
    assert "parsed just now" not in reason


def test_budget_zero_turns_it_off(monkeypatch, capsys, repo):
    monkeypatch.setenv("CGH_ON_DEMAND_BUDGET_MS", "0")
    src = repo / "app.py"
    src.write_text(_source())
    assert _read(monkeypatch, capsys, src, repo) == {}


def test_grep_in_one_new_file_has_the_owner_index_it(monkeypatch, capsys, repo):
    owner = _with_owner(monkeypatch, _FakeOwner(repo))
    src = repo / "app.py"
    src.write_text(_source())
    out = _hook(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"pattern": "handler_2", "path": "app.py"},
        repo,
    )
    assert "app.py was not in the index: indexed just now" in out["additionalContext"]
    assert owner.calls == [("file_summary", {"file_path": "app.py"})]


def test_grep_in_one_new_file_without_owner_is_silent(monkeypatch, capsys, repo):
    src = repo / "app.py"
    src.write_text(_source())
    out = _hook(
        monkeypatch,
        capsys,
        h.cmd_hook_precheck_grep,
        {"pattern": "handler_2", "path": "app.py"},
        repo,
    )
    assert out == {}
