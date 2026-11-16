# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for `cgh read`, the CLI mirror of the read_lines MCP tool:
#              a line range or a --symbol slice of a working-tree file, owner-free.

from __future__ import annotations

import argparse
import json

import pytest

from cgh.cli.commands_query import cmd_read


def _ns(root, file, start=0, end=0, symbol="", as_json=True):
    return argparse.Namespace(
        root=str(root), file=file, start=start, end=end, symbol=symbol, json=as_json
    )


def test_read_command_prints_a_line_range(tmp_path, capsys):
    (tmp_path / "mod.py").write_text("".join(f"line {i}\n" for i in range(1, 21)))
    cmd_read(_ns(tmp_path, "mod.py", start=5, end=7))
    out = json.loads(capsys.readouterr().out)
    assert [e["line"] for e in out["lines"]] == [5, 6, 7]
    assert out["lines"][0]["text"] == "line 5"


def test_read_command_reads_a_symbol(tmp_path, capsys):
    (tmp_path / "mod.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
    )
    cmd_read(_ns(tmp_path, "mod.py", symbol="beta"))
    out = json.loads(capsys.readouterr().out)
    assert any("def beta()" in e["text"] for e in out["lines"])


def test_read_command_errors_out_of_tree(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_read(_ns(tmp_path, "../secret.py"))
    assert exc.value.code == 1
