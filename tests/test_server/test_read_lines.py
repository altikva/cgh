# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the read_lines MCP tool: the bounded working-tree read
#              (line range, whole small file, outline for a large one, a symbol,
#              truncation), the secret guard (git-tracked allowlist + denylist:
#              .cgh/, .git/, .env, key material, git-ignored and untracked paths
#              refused), and that every call lands in call_log so the adoption
#              study counts it as a cgh tool call, not a native Read/sed.

from __future__ import annotations

import json
import subprocess

import pytest

import cgh.server as _srv
from cgh.core.db import reset_connection
from cgh.server.tools_query import register as register_query
from cgh.state import call_log


class _FakeMcp:
    """Records each @tool()-decorated function unchanged so a test can call
    the tool body (with its _logged_tool wrapper) directly."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


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


def _track(root, name, content):
    """Write a file and stage it, so git tracks it (the read allowlist)."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    _git(root, "add", "--", name)
    return path


@pytest.fixture
def read_tool(tmp_path):
    reset_connection()
    call_log.reset_for_tests()
    root = tmp_path.resolve()
    _git(root, "init")
    _srv._root = root
    _srv._conn = None
    m = _FakeMcp()
    register_query(m)
    yield m.tools["read_lines"], root
    reset_connection()
    call_log.reset_for_tests()
    _srv._root = None
    _srv._conn = None


def test_read_lines_returns_a_line_range(read_tool):
    read_lines, root = read_tool
    _track(root, "mod.py", "".join(f"line {i}\n" for i in range(1, 51)))
    out = json.loads(read_lines("mod.py", 10, 12))
    assert out["path"] == "mod.py"
    assert out["start_line"] == 10 and out["end_line"] == 12
    assert [e["line"] for e in out["lines"]] == [10, 11, 12]
    assert out["lines"][0]["text"] == "line 10"
    assert out["total_lines"] == 50


def test_read_lines_whole_small_file_without_a_range(read_tool):
    read_lines, root = read_tool
    _track(root, "tiny.py", "a = 1\nb = 2\n")
    out = json.loads(read_lines("tiny.py"))
    assert [e["text"] for e in out["lines"]] == ["a = 1", "b = 2"]


def test_read_lines_large_file_without_a_range_gives_an_outline(read_tool):
    read_lines, root = read_tool
    body = "".join(f"def fn_{i}():\n    return {i}\n\n\n" for i in range(400))
    _track(root, "big.py", body)
    out = json.loads(read_lines("big.py"))
    assert out.get("outline")
    assert any(e["name"] == "fn_0" for e in out["outline"])
    assert "lines" not in out  # an outline stands in for the body


def test_read_lines_reads_one_symbol(read_tool):
    read_lines, root = read_tool
    _track(
        root,
        "mod.py",
        "import os\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 2\n",
    )
    out = json.loads(read_lines("mod.py", symbol="beta"))
    assert out["symbol"] == "beta"
    assert any("def beta()" in e["text"] for e in out["lines"])
    assert all("alpha" not in e["text"] for e in out["lines"])


def test_read_lines_unknown_symbol_is_an_error(read_tool):
    read_lines, root = read_tool
    _track(root, "mod.py", "def alpha():\n    return 1\n")
    out = json.loads(read_lines("mod.py", symbol="nope"))
    assert "error" in out and "nope" in out["error"]


def test_read_lines_missing_file_is_an_error(read_tool):
    read_lines, _ = read_tool
    out = json.loads(read_lines("does/not/exist.py"))
    assert "error" in out and "exist.py" in out["error"]


def test_read_lines_refuses_a_path_outside_the_repo(read_tool):
    read_lines, _ = read_tool
    out = json.loads(read_lines("../../etc/hosts"))
    assert "error" in out and "outside the repo" in out["error"]


def test_read_lines_refuses_the_state_dir_auth_key(read_tool):
    read_lines, root = read_tool
    (root / ".cgh").mkdir()
    (root / ".cgh" / "auth.key").write_text("s3cr3t-token\n")
    out = json.loads(read_lines(".cgh/auth.key"))
    assert "error" in out and "refused" in out["error"]


def test_read_lines_refuses_a_dotenv_even_when_tracked(read_tool):
    read_lines, root = read_tool
    # Force-track it so only the name denylist (not the allowlist) can refuse it.
    (root / ".env").write_text("STRIPE_SECRET=sk_live_abc\n")
    _git(root, "add", "-f", "--", ".env")
    out = json.loads(read_lines(".env"))
    assert "error" in out and "refused" in out["error"]


def test_read_lines_refuses_key_material(read_tool):
    read_lines, root = read_tool
    (root / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n")
    _git(root, "add", "-f", "--", "server.pem")
    out = json.loads(read_lines("server.pem"))
    assert "error" in out and "refused" in out["error"]


def test_read_lines_refuses_a_gitignored_path(read_tool):
    read_lines, root = read_tool
    _track(root, ".gitignore", "ignored.py\n")
    (root / "ignored.py").write_text("TOKEN = 'x'\n")  # present but not tracked
    out = json.loads(read_lines("ignored.py"))
    assert "error" in out and "not tracked" in out["error"]


def test_read_lines_refuses_an_untracked_file(read_tool):
    read_lines, root = read_tool
    (root / "scratch.py").write_text("x = 1\n")  # on disk, never git-added
    out = json.loads(read_lines("scratch.py"))
    assert "error" in out and "not tracked" in out["error"]


def test_read_lines_tracked_name_with_glob_magic_reads(read_tool):
    """A real filename holding glob metacharacters is matched literally, so a
    tracked `a[b].py` still reads."""
    read_lines, root = read_tool
    _track(root, "a[b].py", "value = 42\n")
    out = json.loads(read_lines("a[b].py"))
    assert out["lines"][0]["text"] == "value = 42"


def test_read_lines_untracked_glob_name_does_not_match_a_tracked_file(read_tool):
    """The allowlist check must treat the path literally: an untracked file
    whose name globs onto a tracked one (`a[1].py` -> `a1.py`) stays refused,
    rather than being served because the pathspec matched a different file."""
    read_lines, root = read_tool
    _track(root, "a1.py", "SECRET = 'tracked-decoy'\n")
    (root / "a[1].py").write_text("TOKEN = 'untracked-secret'\n")  # not git-added
    out = json.loads(read_lines("a[1].py"))
    assert "error" in out and "not tracked" in out["error"]


def test_read_lines_refuses_under_dot_git(read_tool):
    read_lines, _ = read_tool
    out = json.loads(read_lines(".git/config"))
    assert "error" in out and "refused" in out["error"]


def test_read_lines_truncates_at_the_line_cap(read_tool):
    read_lines, root = read_tool
    _track(root, "huge.py", "".join(f"x{i}\n" for i in range(1000)))
    out = json.loads(read_lines("huge.py", 1, 900))
    # slice_lines caps the range at 400 lines; the answer budget may trim it
    # further, and either way next_start_line says where to resume.
    assert out["truncated"] is True
    assert 0 < len(out["lines"]) <= 400
    assert out["next_start_line"] == out["end_line"] + 1


def test_read_lines_is_logged_as_a_cgh_call(read_tool):
    """The adoption study counts call_log rows as cgh tool calls. A read via
    read_lines must land there, so it is attributed to cgh and not to a native
    Read/sed the way a working-tree read would be otherwise."""
    read_lines, root = read_tool
    _track(root, "mod.py", "one\ntwo\nthree\n")
    read_lines("mod.py", 1, 2)
    rows = call_log.get_logs(repo_root=root, tool="read_lines", limit=10)
    assert rows and rows[0]["tool"] == "read_lines"
