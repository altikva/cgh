# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh prs overlap` and the pr_overlap tool. A unified diff maps
#              to base-side line ranges whatever its context size; the gh
#              calls are mocked (never a real network call) and every way gh
#              can be unusable gives a clear message, not a traceback; open
#              PRs pair up as strong (same symbol), indirect (one PR's
#              changed code calls what the other changes) or weak (same file
#              only), on both graph backends.

from __future__ import annotations

import argparse
import json
import subprocess
from types import SimpleNamespace

import pytest

from codegraph.analysis import pr_overlap as po
from codegraph.core.db import get_connection, reset_connection
from codegraph.indexer import index_repo

HELPERS = """import os


def alpha():
    return os.sep


def beta():
    return 2


def gamma():
    return 3
"""

FILES = {
    "helpers.py": HELPERS,
    "b.py": "from helpers import beta\n\n\ndef run_b():\n    return beta()\n",
}


def _line(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), 1) if needle in ln)


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

DIFF = """diff --git a/helpers.py b/helpers.py
index 111..222 100644
--- a/helpers.py
+++ b/helpers.py
@@ -8,7 +8,7 @@ def alpha():


 def beta():
-    return 2
+    return 20


 def gamma():
@@ -20,0 +21,2 @@ def gamma():
+x = 1
+y = 2
diff --git a/schema.sql b/schema.sql
--- a/schema.sql
+++ b/schema.sql
@@ -3,2 +3,1 @@
--- a comment that goes away
 select 1;
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+def fresh():
+    return 1
"""


def test_parse_diff_ranges_uses_base_side_lines():
    ranges = po.parse_diff_ranges(DIFF)
    # The replaced line is base line 11; the insertion after base line 20
    # marks 20-21; a removed "-- comment" line is a body line, not a header.
    assert ranges["helpers.py"] == [(11, 11), (20, 21)]
    assert ranges["schema.sql"] == [(3, 3)]
    # A file the PR adds has no base side: changed as a whole.
    assert ranges["new.py"] is None


def test_pure_insertion_with_context_marks_the_lines_around_it():
    diff = "--- a/m.py\n+++ b/m.py\n@@ -4,4 +4,5 @@\n a\n b\n+new\n c\n d\n"
    assert po.parse_diff_ranges(diff) == {"m.py": [(5, 6)]}


# ---------------------------------------------------------------------------
# gh calls, mocked
# ---------------------------------------------------------------------------


class FakeGh:
    """Stands in for subprocess.run: answers gh by argv, records calls."""

    def __init__(self, answers):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        assert argv[0] == "gh"
        assert kwargs.get("timeout")
        self.calls.append(list(argv))
        key = " ".join(argv[1:3]) if argv[2] != "diff" else f"pr diff {argv[3]}"
        if argv[1:3] == ["pr", "view"]:
            key = f"pr view {argv[3]}"
        code, out, err = self.answers[key]
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)


@pytest.fixture
def gh_on_path(monkeypatch):
    monkeypatch.setattr(po.shutil, "which", lambda name: "/usr/bin/gh")


def test_gh_missing_is_a_clear_message(monkeypatch, tmp_path):
    monkeypatch.setattr(po.shutil, "which", lambda name: None)
    prs, warnings, err = po.fetch_open_prs(str(tmp_path))
    assert prs == [] and warnings == []
    assert "not installed" in err


def test_gh_not_authenticated(monkeypatch, tmp_path, gh_on_path):
    fake = FakeGh(
        {"pr list": (4, "", "To get started with GitHub CLI, run:  gh auth login")}
    )
    monkeypatch.setattr(po.subprocess, "run", fake)
    _, _, err = po.fetch_open_prs(str(tmp_path))
    assert err == po._NOT_AUTHED


def test_no_github_remote(monkeypatch, tmp_path, gh_on_path):
    fake = FakeGh(
        {
            "pr list": (
                1,
                "",
                "none of the git remotes configured for this repository "
                "point to a known GitHub host",
            )
        }
    )
    monkeypatch.setattr(po.subprocess, "run", fake)
    _, _, err = po.fetch_open_prs(str(tmp_path))
    assert err == po._NO_REMOTE


def test_gh_timeout_is_a_message(monkeypatch, tmp_path, gh_on_path):
    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(po.subprocess, "run", boom)
    _, _, err = po.fetch_open_prs(str(tmp_path))
    assert "timed out" in err


def test_fetch_reads_list_then_each_diff(monkeypatch, tmp_path, gh_on_path):
    listed = [
        {"number": 7, "title": "T7", "headRefName": "f/7", "url": "u7"},
        {"number": 9, "title": "T9", "headRefName": "f/9", "url": "u9"},
    ]
    fake = FakeGh(
        {
            "pr list": (0, json.dumps(listed), ""),
            "pr diff 7": (0, DIFF, ""),
            "pr diff 9": (1, "", "HTTP 406: diff exceeded the maximum"),
            "pr view 9": (0, json.dumps({"files": [{"path": "big.py"}]}), ""),
        }
    )
    monkeypatch.setattr(po.subprocess, "run", fake)
    prs, warnings, err = po.fetch_open_prs(str(tmp_path), limit=500)
    assert err is None
    assert [p["number"] for p in prs] == [7, 9]
    assert prs[0]["files"]["helpers.py"] == [(11, 11), (20, 21)]
    # A diff gh cannot return falls back to the file list, file level only.
    assert prs[1]["files"] == {"big.py": None}
    assert prs[1]["file_level_only"] is True
    assert warnings and "PR 9" in warnings[0]
    # Read-only calls only, and the limit is capped.
    assert all(c[1] == "pr" and c[2] in {"list", "diff", "view"} for c in fake.calls)
    assert fake.calls[0][fake.calls[0].index("--limit") + 1] == str(po.MAX_LIMIT)


# ---------------------------------------------------------------------------
# Overlap on a real graph
# ---------------------------------------------------------------------------


@pytest.fixture(params=["duckdb", "sqlite"])
def repo(tmp_path, request, monkeypatch):
    monkeypatch.setenv("CGH_DB", request.param)
    for rel, body in FILES.items():
        (tmp_path / rel).write_text(body, encoding="utf-8")
    reset_connection()
    index_repo(str(tmp_path))
    yield tmp_path
    reset_connection()


def _pr(number, files):
    return {
        "number": number,
        "title": f"T{number}",
        "head": f"f/{number}",
        "files": files,
    }


def test_overlap_levels(repo):
    beta = _line(HELPERS, "return 2")
    gamma = _line(HELPERS, "return 3")
    run_b = _line(FILES["b.py"], "return beta()")
    prs = [
        _pr(1, {"helpers.py": [(beta, beta)]}),  # changes beta
        _pr(2, {"helpers.py": [(beta, beta)]}),  # changes beta too
        _pr(3, {"b.py": [(run_b, run_b)]}),  # run_b calls beta
        _pr(4, {"helpers.py": [(gamma, gamma)]}),  # same file, other symbol
        _pr(5, {"other.py": None}),  # unrelated new file
    ]
    report = po.overlap_report(get_connection(repo), str(repo), prs)
    by_pair = {(p["a"], p["b"]): p for p in report["pairs"]}

    strong = by_pair[(1, 2)]
    assert strong["level"] == po.STRONG
    assert [s["name"] for s in strong["shared_symbols"]] == ["beta"]

    indirect = by_pair[(1, 3)]
    assert indirect["level"] == po.INDIRECT
    row = indirect["indirect"][0]
    assert (row["caller_pr"], row["caller"], row["callee_pr"], row["callee"]) == (
        3,
        "run_b",
        1,
        "beta",
    )
    assert row["caller_file"] == "b.py"

    assert by_pair[(1, 4)]["level"] == po.WEAK
    assert by_pair[(1, 4)]["shared_files"] == ["helpers.py"]
    assert not any(5 in key for key in by_pair)
    # Strongest first.
    assert report["pairs"][0]["level"] == po.STRONG
    assert report["counts"][po.STRONG] == 1
    assert {p["number"]: p["changed_symbols"] for p in report["prs"]}[1] == 1
    assert "## cgh prs overlap" in po.render_markdown(report)


# ---------------------------------------------------------------------------
# CLI: clean failure, never a traceback
# ---------------------------------------------------------------------------


def test_cli_without_gh_fails_cleanly(repo, monkeypatch, capsys):
    from codegraph.cli import commands_prs

    monkeypatch.setattr(po.shutil, "which", lambda name: None)
    args = argparse.Namespace(
        action="overlap", limit=5, json=True, format="md", out="", root=str(repo)
    )
    with pytest.raises(SystemExit) as exc:
        commands_prs.cmd_prs(args)
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "not installed" in out["error"]


def test_cli_reports_overlap_from_mocked_gh(repo, monkeypatch, capsys, gh_on_path):
    from codegraph.cli import commands_prs

    beta = _line(HELPERS, "return 2")
    diff = (
        "--- a/helpers.py\n+++ b/helpers.py\n"
        f"@@ -{beta},1 +{beta},1 @@\n-    return 2\n+    return 22\n"
    )
    listed = [{"number": 1, "title": "a"}, {"number": 2, "title": "b"}]
    fake = FakeGh(
        {
            "pr list": (0, json.dumps(listed), ""),
            "pr diff 1": (0, diff, ""),
            "pr diff 2": (0, diff, ""),
        }
    )
    monkeypatch.setattr(po.subprocess, "run", fake)
    args = argparse.Namespace(
        action="overlap", limit=5, json=True, format="md", out="", root=str(repo)
    )
    commands_prs.cmd_prs(args)
    report = json.loads(capsys.readouterr().out)
    assert report["pairs"][0]["level"] == po.STRONG
