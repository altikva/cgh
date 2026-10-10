# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A graph query answering without an owner refuses an index
#              written in an older graph format. Only lookup and search used
#              to stop, and only because they crashed on a missing column:
#              callers, callees and impact ran on the old graph and printed
#              silently incomplete answers. Every local query now reads the
#              recorded format first, prints the remedy on stderr and exits 1.

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

import codegraph.cli.commands_query as cq
from codegraph.cli.commands_impact import cmd_impact
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo
from codegraph.state.scan_meta import GRAPH_FORMAT


def _git(root, *args):
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path):
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    _git(root, "init")
    (root / "lib.py").write_text(
        "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "one")
    (root / "lib.py").write_text(
        "def helper():\n    return 2\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    _git(root, "commit", "-am", "two")
    reset_connection()
    index_repo(str(root))
    reset_connection()
    yield root
    reset_connection()


def _age(root, graph_format) -> None:
    path = root / ".codegraph" / "scan_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    meta["graph_format"] = graph_format
    path.write_text(json.dumps(meta), encoding="utf-8")


COMMANDS = [
    ("lookup", lambda r: cq.cmd_lookup(argparse.Namespace(root=r, name="helper"))),
    (
        "search",
        lambda r: cq.cmd_search(
            argparse.Namespace(root=r, query="helper", limit=5, offset=0, json=True)
        ),
    ),
    ("callers", lambda r: cq.cmd_callers(argparse.Namespace(root=r, fn_name="helper"))),
    ("callees", lambda r: cq.cmd_callees(argparse.Namespace(root=r, fn_name="run"))),
    ("outline", lambda r: cq.cmd_outline(argparse.Namespace(root=r, file="lib.py"))),
    (
        "endpoints",
        lambda r: cq.cmd_endpoints(
            argparse.Namespace(
                root=r, pattern="", method="", include_tests=False, limit=0, json=True
            )
        ),
    ),
    (
        "impact",
        lambda r: cmd_impact(
            argparse.Namespace(root=r, since="HEAD~1", json=True, format="json", out="")
        ),
    ),
]


@pytest.mark.parametrize("name, run", COMMANDS, ids=[c[0] for c in COMMANDS])
def test_older_format_is_refused_with_the_remedy(repo, capsys, name, run):
    _age(repo, GRAPH_FORMAT - 1)
    with pytest.raises(SystemExit) as exc:
        run(str(repo))
    assert exc.value.code == 1
    out = capsys.readouterr()
    text = out.err + out.out  # impact --json keeps its error on stdout
    assert f"graph format {GRAPH_FORMAT - 1}, this cgh needs {GRAPH_FORMAT}" in text
    assert "cgh index" in text


@pytest.mark.parametrize("name, run", COMMANDS, ids=[c[0] for c in COMMANDS])
def test_current_format_answers(repo, name, run):
    _age(repo, GRAPH_FORMAT)
    run(str(repo))  # no SystemExit
