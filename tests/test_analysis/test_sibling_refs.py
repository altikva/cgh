# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search(repo=, ref=) and file_at_ref read a declared
#              sibling repo (or this one) at a branch it does not have checked
#              out, straight from git. Siblings are granted by the user's
#              global config only: a project's own config, committed or not,
#              never opens another repo. Undeclared repos, unknown refs,
#              option-shaped refs and paths leaving the repo are refused.

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
    """A project, a sibling "api" repo whose `develop` branch holds a class
    its checked-out `main` does not, and a global config (in a temp home)
    that declares the api."""
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

    home = base / "home"
    home.mkdir()
    monkeypatch.setattr("codegraph.core.config.GLOBAL_DIR", home)
    (home / "config.toml").write_text(f'[codegraph]\nsiblings = ["{api}"]\n')

    proj = base / "front"
    proj.mkdir()
    (proj / "page.ts").write_text("export const x = 1\n")
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "add", "page.ts")
    _git(proj, "commit", "-qm", "init")

    terraform._TOPLEVELS.clear()
    _srv._root = proj
    m = _FakeMcp()
    register_query(m)
    yield proj, api, home, m.tools
    _srv._root = None


def test_search_sibling_at_a_branch_not_checked_out(setup):
    *_, t = setup
    out = json.loads(
        t["pattern_search"]("class Association", repo="api", ref="develop")
    )
    assert out["backend"] == "git-grep-ref"
    assert out["repo"] == "api" and len(out["commit"]) == 40
    assert [(g["file"], g["hits"]) for g in out["files"]] == [
        ("app/models.py", [[5, "class Association:"]])
    ]
    assert "scope" not in out["files"][0]
    # The working tree (main) does not have it.
    out = json.loads(t["pattern_search"]("class Association", repo="api"))
    assert out["files"] == []


def test_file_at_ref_reads_a_line_range(setup):
    *_, t = setup
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


def test_file_at_ref_stops_at_the_answer_cap(setup):
    """A long file read whole: the answer stays under the byte cap, ends on
    a whole line and says where to go on."""
    proj, *_, t = setup
    (proj / "big.py").write_text(
        "".join(f"value_{i} = '{'v' * 90}'\n" for i in range(400))
    )
    _git(proj, "add", "big.py")
    _git(proj, "commit", "-qm", "big")
    raw = t["file_at_ref"]("big.py", "main", start_line=1)
    assert len(raw.encode()) <= 10_000
    out = json.loads(raw)
    assert out["truncated"] is True and "hint" in out
    assert out["end_line"] == out["lines"][-1]["line"] < 400
    assert out["next_start_line"] == out["end_line"] + 1
    nxt = json.loads(
        t["file_at_ref"]("big.py", "main", start_line=out["next_start_line"])
    )
    assert nxt["lines"][0]["line"] == out["end_line"] + 1


def _commit(proj: Path, name: str, text: str) -> None:
    (proj / name).write_text(text)
    _git(proj, "add", name)
    _git(proj, "commit", "-qm", name)


_BIG_PY = (
    '"""A module long enough to be outlined."""\n\n\n'
    + "".join(
        f"def fn_{i}(x):\n    return x + {i}  # {'p' * 120}\n\n\n" for i in range(40)
    )
    + "class Box:\n    def open(self):\n        return 1\n\n    def shut(self):\n"
    "        return 0\n"
)


def test_large_file_without_range_answers_an_outline(setup):
    proj, *_, t = setup
    _commit(proj, "mod.py", _BIG_PY)
    raw = t["file_at_ref"]("mod.py", "main")
    out = json.loads(raw)
    assert "lines" not in out and "hint" in out
    assert len(raw.encode()) < len(_BIG_PY.encode()) / 2
    names = {e["name"]: e for e in out["outline"]}
    assert names["fn_0"] == {"name": "fn_0", "kind": "function", "lines": "4-5"}
    assert names["Box.shut"]["kind"] == "method"
    assert out["total_lines"] == len(_BIG_PY.splitlines())


def test_large_file_in_an_unparsed_language_answers_its_head(setup):
    proj, *_, t = setup
    _commit(proj, "notes.xyz", "".join(f"row {i} {'q' * 80}\n" for i in range(200)))
    out = json.loads(t["file_at_ref"]("notes.xyz", "main"))
    assert "outline" not in out and "lines" not in out
    lines = [x["line"] for x in out["head"]]
    # The first lines in order, as many as fit 4 KB as received.
    assert lines == list(range(1, len(lines) + 1)) and 20 <= len(lines) <= 30
    assert out["total_lines"] == 200 and "start_line" in out["hint"]


def test_small_file_stays_whole(setup):
    proj, *_, t = setup
    _commit(proj, "small.py", "def a():\n    return 1\n")
    out = json.loads(t["file_at_ref"]("small.py", "main"))
    assert "outline" not in out
    assert [x["text"] for x in out["lines"]] == ["def a():", "    return 1"]


def test_symbol_answers_its_lines_only(setup):
    proj, *_, t = setup
    _commit(proj, "mod.py", _BIG_PY)
    out = json.loads(t["file_at_ref"]("mod.py", "main", symbol="Box.open"))
    assert out["symbol"] == "Box.open" and out["kind"] == "method"
    assert [x["text"].strip() for x in out["lines"]] == ["def open(self):", "return 1"]
    # A bare method name finds it too.
    out = json.loads(t["file_at_ref"]("mod.py", "main", symbol="shut"))
    assert out["symbol"] == "Box.shut"
    assert out["lines"][0]["text"].strip() == "def shut(self):"


def test_symbol_on_a_sibling_branch(setup):
    *_, t = setup
    out = json.loads(
        t["file_at_ref"]("app/models.py", "develop", repo="api", symbol="Association")
    )
    assert out["repo"] == "api"
    assert (out["start_line"], out["end_line"]) == (5, 6)


def test_unknown_symbol_is_refused(setup):
    proj, *_, t = setup
    _commit(proj, "mod.py", _BIG_PY)
    out = json.loads(t["file_at_ref"]("mod.py", "main", symbol="fn_9x"))
    assert "no symbol 'fn_9x'" in out["error"]
    out = json.loads(t["file_at_ref"]("notes.xyz", "main", symbol="a"))
    assert "error" in out


@pytest.mark.parametrize(
    ("start", "end", "needle"),
    [(9, 3, "before start_line"), (50, 0, "past the end"), (-2, 0, "positive")],
)
def test_bad_range_is_refused(setup, start, end, needle):
    *_, t = setup
    out = json.loads(
        t["file_at_ref"](
            "app/models.py", "develop", repo="api", start_line=start, end_line=end
        )
    )
    assert needle in out["error"]


def test_end_line_alone_reads_from_the_top(setup):
    proj, *_, t = setup
    _commit(proj, "mod.py", _BIG_PY)
    out = json.loads(t["file_at_ref"]("mod.py", "main", end_line=2))
    assert [x["line"] for x in out["lines"]] == [1, 2]


def test_own_repo_at_a_ref(setup):
    *_, t = setup
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
    *_, t = setup
    out = json.loads(t["pattern_search"]("x", **kwargs))
    assert needle in out["error"]
    assert "files" not in out


@pytest.mark.parametrize("path", ["../front/page.ts", "/etc/passwd", "app/../../x", ""])
def test_paths_stay_inside_the_repo(setup, path):
    *_, t = setup
    out = json.loads(t["file_at_ref"](path, "develop", repo="api"))
    assert "error" in out


def test_missing_file_at_ref(setup):
    *_, t = setup
    out = json.loads(t["file_at_ref"]("app/none.py", "develop", repo="api"))
    assert "does not exist" in out["error"]


def test_sibling_named_by_path_too(setup):
    proj, api, _home, _t = setup
    assert refs.resolve_repo(proj, str(api)) == ("api", api)


@pytest.mark.parametrize("tracked", [False, True])
def test_a_project_config_never_grants_access(setup, tracked):
    """Whatever its git status, a project's .codegraph/config.toml can come
    with a clone, a copied store or a seeded worktree: its siblings are
    ignored, and the error says where they belong."""
    proj, api, home, t = setup
    (home / "config.toml").unlink()
    (proj / ".codegraph").mkdir()
    (proj / ".codegraph" / "config.toml").write_text(
        f'[codegraph]\nsiblings = ["{api}"]\n'
    )
    if tracked:
        _git(proj, "add", "-f", ".codegraph/config.toml")
        _git(proj, "commit", "-qm", "ship config")
    out = json.loads(t["pattern_search"]("class", repo="api", ref="develop"))
    assert "not a declared sibling" in out["error"]
    assert "~/.codegraph/config.toml" in out["error"]
    assert "is ignored" in out["error"]
    assert refs.sibling_repos() == {}


def test_relative_global_entry_is_ignored(setup):
    """A relative entry would name a different repo from every project."""
    _proj, _api, home, _t = setup
    (home / "config.toml").write_text('[codegraph]\nsiblings = ["../api"]\n')
    assert refs.sibling_repos() == {}


def test_git_redirect_env_is_not_inherited(setup, monkeypatch, tmp_path):
    """GIT_DIR in the owner's environment must not swap the repo read."""
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    *_, t = setup
    out = json.loads(
        t["pattern_search"]("class Association", repo="api", ref="develop")
    )
    assert [g["file"] for g in out["files"]] == ["app/models.py"]
