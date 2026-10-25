# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-25
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: pattern_search answers the same hits whichever backend runs
#              it: ripgrep, git grep with -P, git grep with -E (Perl classes
#              spelled in POSIX) and the Python re fallback. git grep used to
#              run every regex with -E, where \w, \d and \b match nothing, so
#              a machine without ripgrep got an empty answer. Globs get the
#              same check: one without a "/" matches at any depth everywhere.
#              A backend missing on this machine is skipped with the reason.

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from codegraph.analysis import pattern as P

_FILES = {
    "pkg/answers.py": (
        "def search_answer(x):\n"
        "    return 123\n"
        "DEF Upper_Answer():\n"
        "def answer():\n"
        "foo = 1\n"
        "foobar = 2\n"
        "a_foo_b = 3\n"
        "call(foo)\n"
        "id = 12\n"
    ),
    "pkg/sub/module_deep.py": "abc_x = ABC_X\nx1_x\nneedle\n",
    "module_top.py": "def outline_answer(): pass\nneedle\n",
    "pkg/notes.txt": "tabs\tand  spaces\nlazy <a> <b>\nneedle\n",
    "pkg/sub/other.py": "needle\n",
}

PATTERNS = [
    r"def \w+_answer",
    r"\d{3}",
    r"\bfoo\b",
    r"[a-z]+_x",
    r"\w+?_x",
    r"<.+?>",
    r"\s{2,}",
    r"[\w-]+ = \d+",
    r"[^\s=]+ = 1",
]

GLOBS = [
    "module_*.py",
    "*.py",
    "pkg/*.py",
    "pkg/**",
    "*.{py,txt}",
    "/module_top.py",
    "sub/*.py",
]


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not on PATH")
    for rel, text in _FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    return tmp_path.resolve()


def _hide(monkeypatch, *tools):
    real = shutil.which
    monkeypatch.setattr(
        P.shutil, "which", lambda name, *a, **k: None if name in tools else real(name)
    )


def _set_backend(name, monkeypatch):
    """Leave only the backend ``name`` reachable, or skip saying why not."""
    if name == "rg":
        if shutil.which("rg") is None:
            pytest.skip("ripgrep not on PATH")
    elif name == "git-pcre":
        if not P.git_has_pcre():
            pytest.skip("git built without PCRE: git grep -P unavailable")
        _hide(monkeypatch, "rg")
    elif name == "git-ere":
        _hide(monkeypatch, "rg")
        monkeypatch.setattr(P, "git_has_pcre", lambda: False)
    else:
        _hide(monkeypatch, "rg", "git")


def _search(repo, monkeypatch, backend, pattern, glob=""):
    with monkeypatch.context() as m:
        _set_backend(backend, m)
        hits, used = P.pattern_search(repo, pattern, glob=glob, max_results=500)
    return used, sorted(
        (Path(h.file).resolve().relative_to(repo).as_posix(), h.line) for h in hits
    )


def _reference(repo, monkeypatch, pattern, glob=""):
    return _search(repo, monkeypatch, "python", pattern, glob)[1]


BACKENDS = ["rg", "git-pcre", "git-ere", "python"]
_EXPECTED_BACKEND = {
    "rg": "rg",
    "git-pcre": "git-grep",
    "git-ere": "git-grep",
    "python": "python-fallback",
}


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("pattern", PATTERNS)
def test_regex_parity(repo, monkeypatch, backend, pattern):
    expected = _reference(repo, monkeypatch, pattern)
    assert expected, f"fixture holds no line for {pattern!r}"
    used, got = _search(repo, monkeypatch, backend, pattern)
    if backend == "git-ere" and P.to_posix_ere(pattern) is None:
        # ERE cannot say it: the Python backend answers instead.
        assert used == "python-fallback"
    else:
        assert used == _EXPECTED_BACKEND[backend]
    assert got == expected


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("glob", GLOBS)
def test_glob_parity(repo, monkeypatch, backend, glob):
    expected = _reference(repo, monkeypatch, "needle", glob)
    used, got = _search(repo, monkeypatch, backend, "needle", glob)
    assert used == _EXPECTED_BACKEND[backend]
    assert got == expected


def test_glob_without_slash_reaches_subfolders(repo, monkeypatch):
    files = [f for f, _ in _reference(repo, monkeypatch, "needle", "module_*.py")]
    assert files == ["module_top.py", "pkg/sub/module_deep.py"]


def test_glob_with_slash_is_anchored(repo, monkeypatch):
    assert _reference(repo, monkeypatch, "needle", "sub/*.py") == []
    files = [f for f, _ in _reference(repo, monkeypatch, "needle", "pkg/*.py")]
    assert files == []  # "*" stays inside pkg/, which holds no .py with needle
    files = [f for f, _ in _reference(repo, monkeypatch, "needle", "pkg/**")]
    assert files == ["pkg/notes.txt", "pkg/sub/module_deep.py", "pkg/sub/other.py"]


@pytest.mark.parametrize(
    ("pattern", "ere"),
    [
        (r"def \w+_answer", "def [[:alnum:]_]+_answer"),
        (r"\d{3}", "[0-9]{3}"),
        (r"[\w-]+", "[[:alnum:]_-]+"),
        (r"[^\s]+\S", "[^[:space:]]+[^[:space:]]"),
        (r"a\.b\/c", r"a\.b/c"),
        (r"\bfoo\b", None),
        (r"a.*?b", None),
        (r"(?:a|b)", None),
        (r"[^\W]", None),
    ],
)
def test_to_posix_ere(pattern, ere):
    assert P.to_posix_ere(pattern) == ere
