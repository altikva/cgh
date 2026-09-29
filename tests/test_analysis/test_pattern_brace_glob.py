# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-28
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search with a brace glob such as "*.{ts,vue}" on a
#              machine without ripgrep. git pathspecs and fnmatch have no
#              brace syntax, so the glob matched no file and git grep returned
#              a clean empty result: agents got nothing and fell back to grep.
#              Both non-ripgrep backends are covered, over a real git repo.

from __future__ import annotations

import shutil
import subprocess

import pytest

from codegraph.analysis import pattern as P


@pytest.mark.parametrize(
    ("glob", "expanded"),
    [
        ("*.py", ["*.py"]),
        ("*.{ts,vue}", ["*.ts", "*.vue"]),
        ("src/{api,web}/*.{ts,vue}", [
            "src/api/*.ts", "src/api/*.vue", "src/web/*.ts", "src/web/*.vue",
        ]),
        ("*.{ts,{vue,tsx}}", ["*.ts", "*.vue", "*.tsx"]),
        ("*.{ts,ts}", ["*.ts"]),
        # Not an alternation: the braces stay literal characters.
        ("{only}.py", ["{only}.py"]),
        ("unclosed{a,b.py", ["unclosed{a,b.py"]),
    ],
)  # fmt: skip
def test_expand_braces(glob, expanded):
    assert P._expand_braces(glob) == expanded


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    for name in ("app.ts", "Card.vue", "tool.py"):
        (tmp_path / name).write_text("needle here\n", encoding="utf-8")
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", "-C", str(tmp_path), *a], check=True, capture_output=True
    )
    run("init", "-q")
    run("add", "-A")
    return tmp_path


def _without(monkeypatch, *tools):
    real = shutil.which
    monkeypatch.setattr(
        P.shutil, "which", lambda name, *a, **k: None if name in tools else real(name)
    )


def _files(hits):
    return sorted(h.file.rsplit("/", 1)[-1] for h in hits)


def test_brace_glob_with_git_grep(repo, monkeypatch):
    _without(monkeypatch, "rg")
    hits, backend = P.pattern_search(repo, "needle", glob="*.{ts,vue}")
    assert backend == "git-grep"
    assert _files(hits) == ["Card.vue", "app.ts"]


def test_brace_glob_with_the_python_fallback(repo, monkeypatch):
    _without(monkeypatch, "rg", "git")
    hits, backend = P.pattern_search(repo, "needle", glob="*.{ts,vue}")
    assert backend == "python-fallback"
    assert _files(hits) == ["Card.vue", "app.ts"]


def test_plain_glob_is_unchanged(repo, monkeypatch):
    _without(monkeypatch, "rg")
    hits, _ = P.pattern_search(repo, "needle", glob="*.py")
    assert _files(hits) == ["tool.py"]
