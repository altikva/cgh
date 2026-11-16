# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for `cgh read`, the CLI mirror of the read_lines MCP tool:
#              a line range or a --symbol slice of a git-tracked working-tree
#              file, owner-free, and the secret guard (out-of-tree / untracked).

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from cgh.cli.commands_query import cmd_read


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init")
    return tmp_path


def _track(root, name, content):
    (root / name).write_text(content)
    _git(root, "add", "--", name)


def _ns(root, file, start=0, end=0, symbol="", as_json=True):
    return argparse.Namespace(
        root=str(root), file=file, start=start, end=end, symbol=symbol, json=as_json
    )


def test_read_command_prints_a_line_range(repo, capsys):
    _track(repo, "mod.py", "".join(f"line {i}\n" for i in range(1, 21)))
    cmd_read(_ns(repo, "mod.py", start=5, end=7))
    out = json.loads(capsys.readouterr().out)
    assert [e["line"] for e in out["lines"]] == [5, 6, 7]
    assert out["lines"][0]["text"] == "line 5"


def test_read_command_reads_a_symbol(repo, capsys):
    _track(
        repo, "mod.py", "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
    )
    cmd_read(_ns(repo, "mod.py", symbol="beta"))
    out = json.loads(capsys.readouterr().out)
    assert any("def beta()" in e["text"] for e in out["lines"])


def test_read_command_errors_out_of_tree(repo, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_read(_ns(repo, "../secret.py"))
    assert exc.value.code == 1


def test_read_command_errors_on_untracked_file(repo, capsys):
    (repo / "scratch.py").write_text("x = 1\n")  # never git-added
    with pytest.raises(SystemExit) as exc:
        cmd_read(_ns(repo, "scratch.py"))
    assert exc.value.code == 1
    assert "not tracked" in capsys.readouterr().out
