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
def setup(tmp_path, monkeypatch):
    """A project and a sibling "api" repo. The api's `develop` branch holds
    a class its checked-out `main` does not. The global config is an empty
    directory, not the user's."""
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    base = tmp_path.resolve()
    (base / "home").mkdir()
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", base / "home")
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


def test_siblings_of_a_committed_config_are_ignored(setup):
    """A config.toml shipped with the repo must not open other repos: a
    cloned repo could otherwise point the agent at any git repo on disk."""
    proj, _api, t = setup
    _git(proj, "add", "-f", ".codegraph/config.toml")
    _git(proj, "commit", "-qm", "ship config")
    out = json.loads(t["pattern_search"]("class", repo="api", ref="develop"))
    assert "tracked by git" in out["error"]
    assert refs.sibling_repos(proj) == {}
    # This repo itself stays readable at a ref.
    assert "error" not in json.loads(t["file_at_ref"]("page.ts", "main"))


def test_differently_cased_committed_config_is_not_local(setup):
    """On a case-insensitive filesystem a committed `.CodeGraph/Config.toml`
    is the file cgh reads; the tracked check must match it too."""
    proj, _api, _t = setup
    (proj / ".codegraph" / "config.toml").unlink()
    (proj / ".codegraph").rmdir()
    (proj / ".CodeGraph").mkdir()
    (proj / ".CodeGraph" / "Config.toml").write_text(
        '[codegraph]\nsiblings = ["../api"]\n'
    )
    _git(proj, "-c", "core.ignorecase=false", "add", "-f", ".CodeGraph/Config.toml")
    _git(proj, "commit", "-qm", "ship")
    (proj / ".CodeGraph").rename(proj / ".tmp")
    (proj / ".tmp").rename(proj / ".codegraph")
    (proj / ".codegraph" / "Config.toml").rename(proj / ".codegraph" / "config.toml")
    assert not refs._project_config_is_local(proj)
    assert refs.sibling_repos(proj) == {}


def test_symlinked_codegraph_dir_is_not_local(setup, tmp_path):
    proj, _api, _t = setup
    elsewhere = tmp_path / "elsewhere"
    (proj / ".codegraph").rename(elsewhere)
    (proj / ".codegraph").symlink_to(elsewhere)
    assert not refs._project_config_is_local(proj)
    assert refs.sibling_repos(proj) == {}


def test_git_redirect_env_cannot_hide_a_tracked_config(setup, monkeypatch):
    proj, _api, _t = setup
    _git(proj, "add", "-f", ".codegraph/config.toml")
    _git(proj, "commit", "-qm", "ship config")
    # An index without the file, handed in through the environment.
    monkeypatch.setenv("GIT_INDEX_FILE", str(proj / "empty.index"))
    assert not refs._project_config_is_local(proj)


def test_global_config_siblings_always_count(setup):
    """~/.codegraph/config.toml is the user's own: its siblings count even
    when the project's config is committed."""
    proj, api, t = setup
    _git(proj, "add", "-f", ".codegraph/config.toml")
    _git(proj, "commit", "-qm", "ship config")
    from codegraph.core import config as cfg

    (cfg.GLOBAL_DIR / "config.toml").write_text(f'[codegraph]\nsiblings = ["{api}"]\n')
    out = json.loads(
        t["pattern_search"]("class Association", repo="api", ref="develop")
    )
    assert [h["file"] for h in out["hits"]] == ["app/models.py"]


def test_missing_file_at_ref(setup):
    _proj, _api, t = setup
    out = json.loads(t["file_at_ref"]("app/none.py", "develop", repo="api"))
    assert "does not exist" in out["error"]


def test_sibling_named_by_path_too(setup):
    _proj, api, _t = setup
    name, top = refs.resolve_repo(_srv._root, str(api))
    assert (name, top) == ("api", api)
