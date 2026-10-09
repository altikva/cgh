# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh impact` on a code file starts from the symbols the diff
#              touches. A one-line edit inside a function used to mark every
#              symbol of the file changed and pull in the file's whole
#              transitive radius. Now the changed symbols are the innermost
#              functions and classes a hunk meets (an edit inside a method
#              marks the method, not its class), and the radius is the callers
#              of those symbols, plus the file's direct importers when a class
#              changed outside its methods; a change outside any symbol keeps
#              the whole-file radius.

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from codegraph.analysis.impact import build_impact_report
from codegraph.cli.commands_impact import cmd_impact
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

HELPERS = """import os


def alpha():
    return os.sep


def beta():
    return 2


class Box:
    size = 3

    def open(self):
        return 4
"""

FILES = {
    "helpers.py": HELPERS,
    # imports helpers, calls alpha
    "a.py": "from helpers import alpha\n\n\ndef run_a():\n    return alpha()\n",
    # imports helpers, calls beta
    "b.py": "from helpers import beta\n\n\ndef run_b():\n    return beta()\n",
    # only reaches helpers through a.py
    "c.py": "from a import run_a\n\n\ndef run_c():\n    return run_a()\n",
    # calls Box.open
    "d.py": "from helpers import Box\n\n\ndef run_d():\n    return Box().open()\n",
    # builds a Box, never calls open
    "e.py": "from helpers import Box\n\n\ndef run_e():\n    return Box()\n",
}


def _line(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), 1) if needle in ln)


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", *args],
        cwd=str(root),
        check=True,
        capture_output=True,
    )


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(tmp_path, request, monkeypatch):
    monkeypatch.setenv("CGH_DB", request.param)
    for rel, body in FILES.items():
        (tmp_path / rel).write_text(body, encoding="utf-8")
    reset_connection()
    index_repo(str(tmp_path))
    yield tmp_path
    reset_connection()


def _report(root, entry):
    return build_impact_report(get_connection(root), str(root), [entry])


def _files(report):
    return {r["file"] for r in report["impacted"]}


def test_edit_inside_a_function_marks_only_that_function(repo):
    line = _line(HELPERS, "return 2")
    report = _report(repo, f"helpers.py#L{line}")
    assert [s["name"] for s in report["changed_symbols"]] == ["beta"]
    # The callers of beta only: a.py imports helpers.py but never calls
    # beta, and c.py only reaches alpha through a.py.
    assert _files(report) == {"b.py"}


def test_change_outside_any_symbol_keeps_the_whole_file_radius(repo):
    report = _report(repo, "helpers.py#L1")
    assert report["changed_symbols"] == []
    assert _files(report) == {"a.py", "b.py", "c.py", "d.py", "e.py"}


def test_class_attribute_change_walks_the_class_methods(repo):
    line = _line(HELPERS, "size = 3")
    report = _report(repo, f"helpers.py#L{line}")
    assert [s["name"] for s in report["changed_symbols"]] == ["Box"]
    # A class changed outside its methods reaches the file's importers.
    assert _files(report) == {"a.py", "b.py", "d.py", "e.py"}


def test_edit_inside_a_method_marks_only_that_method(repo):
    # Box.open has one caller (d.py); e.py builds a Box but never calls
    # open, so the class must not count as changed and pull it in.
    line = _line(HELPERS, "return 4")
    report = _report(repo, f"helpers.py#L{line}")
    assert [s["name"] for s in report["changed_symbols"]] == ["open"]
    assert _files(report) == {"d.py"}


def test_hunk_over_a_method_and_a_class_line_marks_both(repo):
    lo, hi = _line(HELPERS, "size = 3"), _line(HELPERS, "return 4")
    report = _report(repo, f"helpers.py#L{lo}-{hi}")
    assert [s["name"] for s in report["changed_symbols"]] == ["Box", "open"]


def test_no_ranges_means_the_whole_file(repo):
    report = _report(repo, "helpers.py")
    assert {s["name"] for s in report["changed_symbols"]} >= {"alpha", "beta", "Box"}
    assert _files(report) == {"a.py", "b.py", "c.py", "d.py", "e.py"}


def test_cli_uses_hunks_of_uncommitted_edits(repo, capsys):
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "helpers.py").write_text(
        HELPERS.replace("return 2", "return 20"), encoding="utf-8"
    )
    reset_connection()
    index_repo(str(repo))
    reset_connection()
    cmd_impact(argparse.Namespace(root=str(repo), since="HEAD", json=True, format="md"))
    report = json.loads(capsys.readouterr().out)
    assert report["since_changed"] == ["helpers.py"]
    assert [s["name"] for s in report["changed_symbols"]] == ["beta"]
    assert _files(report) == {"b.py"}
