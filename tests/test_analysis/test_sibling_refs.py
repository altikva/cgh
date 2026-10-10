# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search(repo=, ref=) and file_at_ref read a declared
#              sibling repo (or this one) at a branch it does not have checked
#              out, straight from git. Undeclared repos, unknown refs, option-
#              shaped refs and paths leaving the repo are refused.

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import codegraph.server as _srv
from codegraph.analysis import refs, terraform
from codegraph.server.tools_query import register as register_query

_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "PATH": "/usr/bin:/bin:/usr/local/bin"}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        env=_ENV,
    )


class _FakeMcp:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture
def setup(tmp_path):
    """A project and a sibling "api" repo. The api's `develop` branch holds
    a class its checked-out `main` does not."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    base = tmp_path.resolve()
    api = base / "api"
    (api / "app").mkdir(parents=True)
    (api / "app" / "models.py").write_text("class Donor:\n    pass\n")
    _git(api, "init", "-q", "-b", "main")
    _git(api, "add", "-A")
    _git(api, "commit", "-qm", "main")
    _git(api, "checkout", "-q", "-b", "develop")
    (api / "app" / "models.py").write_text(
        "class Donor:\n    pass\n\n\nclass Association:\n    name = 'x'\n"
    )
    _git(api, "commit", "-qam", "develop")
    _git(api, "checkout", "-q", "main")

    proj = base / "front"
    (proj / ".codegraph").mkdir(parents=True)
    (proj / "page.ts").write_text("export const x = 1\n")
    (proj / ".codegraph" / "config.toml").write_text(
        '[codegraph]\nsiblings = ["../api"]\n'
    )
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "add", "page.ts")
    _git(proj, "commit", "-qm", "init")

    terraform._TOPLEVELS.clear()
    _srv._root = proj
    m = _FakeMcp()
    register_query(m)
    yield proj, api, m.tools
    _srv._root = None


def test_search_sibling_at_a_branch_not_checked_out(setup):
    _proj, _api, t = setup
    out = json.loads(
        t["pattern_search"]("class Association", repo="api", ref="develop")
    )
    assert out["backend"] == "git-grep-ref"
    assert out["repo"] == "api" and len(out["commit"]) == 40
    assert [(h["file"], h["line"]) for h in out["hits"]] == [("app/models.py", 5)]
    # The working tree (main) does not have it.
    out = json.loads(t["pattern_search"]("class Association", repo="api"))
    assert out["hits"] == []


def test_file_at_ref_reads_a_line_range(setup):
    _proj, _api, t = setup
    out = json.loads(
        t["file_at_ref"](
            "app/models.py", "develop", repo="api", start_line=5, end_line=6
        )
    )
    assert [(x["line"], x["text"]) for x in out["lines"]] == [
        (5, "class Association:"),
        (6, "    name = 'x'"),
    ]
    assert out["total_lines"] == 6 and out["truncated"] is False


def test_own_repo_at_a_ref(setup):
    _proj, _api, t = setup
    out = json.loads(t["file_at_ref"]("page.ts", "main"))
    assert out["repo"] is None
    assert out["lines"][0]["text"] == "export const x = 1"


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"repo": "elsewhere", "ref": "develop"}, "not a declared sibling"),
        ({"repo": "api", "ref": "nope"}, "not found"),
        ({"repo": "api", "ref": "--output=/tmp/x"}, "not found"),
        ({"repo": "api", "ref": "develop..main"}, "not found"),
    ],
)
def test_refusals(setup, kwargs, needle):
    _proj, _api, t = setup
    out = json.loads(t["pattern_search"]("x", **kwargs))
    assert needle in out["error"]
    assert "hits" not in out


@pytest.mark.parametrize("path", ["../front/page.ts", "/etc/passwd", "app/../../x", ""])
def test_paths_stay_inside_the_repo(setup, path):
    _proj, _api, t = setup
    out = json.loads(t["file_at_ref"](path, "develop", repo="api"))
    assert "error" in out


def test_missing_file_at_ref(setup):
    _proj, _api, t = setup
    out = json.loads(t["file_at_ref"]("app/none.py", "develop", repo="api"))
    assert "does not exist" in out["error"]


def test_sibling_named_by_path_too(setup):
    _proj, api, _t = setup
    name, top = refs.resolve_repo(_srv._root, str(api))
    assert (name, top) == ("api", api)
