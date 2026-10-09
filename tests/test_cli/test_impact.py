# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CLI tests for `cgh impact`. Builds a tmp git repo, indexes it,
#              changes a file in a second commit, then runs cmd_impact (JSON
#              mode) and asserts stdout is valid JSON listing the changed
#              file. Git identity is pinned with -c so the test is
#              deterministic and does not depend on the host config.

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from codegraph.cli.commands_impact import cmd_impact
from codegraph.core.db import reset_connection
from codegraph.indexer import index_repo


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


@pytest.fixture
def impact_repo(tmp_path):
    """A git repo with two commits; the second changes app.py."""
    root = tmp_path
    _git(root, "init")
    (root / "lib.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "app.py").write_text(
        "import lib\n\n\ndef run():\n    return lib.helper()\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")

    # Second commit changes app.py so HEAD~1 diff yields it.
    (root / "app.py").write_text(
        "import lib\n\n\ndef run():\n    return lib.helper() + 1\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "tweak app")

    reset_connection()
    index_repo(str(root))
    reset_connection()

    yield root

    reset_connection()


def test_impact_json_lists_changed_file(impact_repo, capsys):
    root = impact_repo
    args = argparse.Namespace(root=str(root), since="HEAD~1", json=True, format="md")
    cmd_impact(args)

    captured = capsys.readouterr()
    # stdout must be valid JSON (stderr carries the banner / notes).
    report = json.loads(captured.out)

    assert report["since"] == "HEAD~1"
    assert any(f.endswith("app.py") for f in report["since_changed"])
    # Report keys a PR bot relies on are present.
    for key in ("impacted", "endpoints", "tests_to_run", "changed_symbols"):
        assert key in report


def test_impact_since_head_sees_the_uncommitted_work(impact_repo, capsys):
    """--since compares the working tree: staged, unstaged and untracked
    files count, not only commits."""
    root = impact_repo
    (root / "lib.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    (root / "staged.py").write_text("def s():\n    return 1\n", encoding="utf-8")
    _git(root, "add", "staged.py")
    (root / "fresh.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    args = argparse.Namespace(root=str(root), since="HEAD", json=True, format="md")
    cmd_impact(args)
    report = json.loads(capsys.readouterr().out)
    assert sorted(report["since_changed"]) == ["fresh.py", "lib.py", "staged.py"]
    # app.py imports lib.py and calls lib.helper().
    assert "app.py" in {r["file"] for r in report["impacted"]}

    # Committed and uncommitted work together against an older ref.
    args = argparse.Namespace(root=str(root), since="HEAD~1", json=True, format="md")
    cmd_impact(args)
    report = json.loads(capsys.readouterr().out)
    assert sorted(report["since_changed"]) == [
        "app.py",
        "fresh.py",
        "lib.py",
        "staged.py",
    ]


def test_impact_follows_calls_and_imports_in_function_bodies(tmp_path, capsys):
    """A test importing the changed file inside a test function, and code
    reaching it only through calls, are in the report."""
    root = tmp_path
    _git(root, "init")
    files = {
        "pkg/__init__.py": "",
        "pkg/cipher.py": "def encrypt(x):\n    return x\n",
        "pkg/service.py": (
            "class Service:\n"
            "    def __init__(self):\n"
            "        from pkg import cipher\n\n"
            "        self.c = cipher\n\n"
            "    def run(self, x):\n"
            "        from pkg.cipher import encrypt\n\n"
            "        return encrypt(x)\n"
        ),
        "pkg/api.py": (
            "from pkg.service import Service\n\n\n"
            "def handle(x):\n    return Service().run(x)\n"
        ),
        "tests/__init__.py": "",
        "tests/conftest.py": (
            "def cipher_fixture():\n"
            "    from pkg.cipher import encrypt\n\n"
            "    return encrypt(1)\n"
        ),
        "tests/test_local.py": (
            "def test_encrypt():\n"
            "    from pkg.cipher import encrypt\n\n"
            "    assert encrypt(1) == 1\n"
        ),
        "tests/test_api.py": (
            "from pkg.api import handle\n\n\n"
            "def test_handle():\n    assert handle(1) == 1\n"
        ),
        "tests/test_other.py": "from tests.conftest import cipher_fixture\n",
    }
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    reset_connection()
    index_repo(str(root))
    reset_connection()
    (root / "pkg/cipher.py").write_text(
        "def encrypt(x):\n    return x + 0\n", encoding="utf-8"
    )

    args = argparse.Namespace(root=str(root), since="HEAD", json=True, format="md")
    cmd_impact(args)
    report = json.loads(capsys.readouterr().out)
    reset_connection()
    impacted = {r["file"] for r in report["impacted"]}
    assert {"pkg/service.py", "pkg/api.py", "tests/test_local.py"} <= impacted
    tests = {t["file"] for t in report["tests_to_run"]}
    assert {"tests/test_local.py", "tests/test_api.py"} <= tests
    # A file importing the changed one only inside a function is a leaf of
    # the import walk: what imports it is not pulled in through it.
    assert "tests/test_other.py" not in impacted


def test_impact_missing_index_fails_cleanly(tmp_path, capsys):
    # No .codegraph/ -> graceful JSON error, exit 1.
    _git(tmp_path, "init")
    (tmp_path / "x.py").write_text("y = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c1")
    (tmp_path / "x.py").write_text("y = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "c2")

    args = argparse.Namespace(
        root=str(tmp_path), since="HEAD~1", json=True, format="md"
    )
    with pytest.raises(SystemExit) as exc:
        cmd_impact(args)
    assert exc.value.code == 1

    report = json.loads(capsys.readouterr().out)
    assert "error" in report
