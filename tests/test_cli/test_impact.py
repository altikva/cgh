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
