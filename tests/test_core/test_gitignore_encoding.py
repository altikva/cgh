# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh init keeps .cgh/ out of version control through the repo's
#              local .git/info/exclude, never the tracked .gitignore: an init
#              in a worktree must not leave an unrequested diff that blocks its
#              teardown. It must also not crash on a non-UTF-8 .gitignore (a
#              CP1252 em dash copied from a doc), which git, not cgh, now reads.

from __future__ import annotations

import subprocess

from cgh.core.config import init_project
from cgh.state.auth import ensure_gitignore_has_auth_key, ensure_locally_ignored

# 0x97 is an em dash in CP1252 and an invalid UTF-8 start byte.
_CP1252_GITIGNORE = b"# Landing zone \x97 generated, do not edit\n*.tfstate\n"


def _git_init(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_init_leaves_the_tracked_gitignore_untouched(tmp_path):
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_bytes(_CP1252_GITIGNORE)
    before = (tmp_path / ".gitignore").read_bytes()

    init_project(tmp_path)  # must not raise on the bad byte

    # The committed .gitignore is left exactly as it was: no unrequested diff.
    assert (tmp_path / ".gitignore").read_bytes() == before
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(
        encoding="utf-8", errors="replace"
    )
    assert ".cgh/" in exclude
    chk = subprocess.run(["git", "check-ignore", "-q", ".cgh/"], cwd=tmp_path)
    assert chk.returncode == 0  # .cgh/ is actually ignored


def test_auth_key_is_ignored_locally_not_in_the_gitignore(tmp_path):
    _git_init(tmp_path)
    ensure_gitignore_has_auth_key(tmp_path)  # must not raise
    assert not (tmp_path / ".gitignore").exists()  # never created/touched
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "auth.key" in exclude


def test_ensure_locally_ignored_is_a_noop_outside_git(tmp_path):
    # No git repo: nothing to ignore, and no tracked file is written.
    assert ensure_locally_ignored(tmp_path, ".cgh/") is False
    assert not (tmp_path / ".gitignore").exists()


def test_guard_cleanup_tolerates_non_utf8_bobignore(tmp_path):
    """cgh init counts, and `cgh guard --remove-rules` removes, the managed
    .bobignore block older versions wrote; a CP1252 byte in the user's part
    must not crash either read."""
    from cgh.state.guard import cleanup_guard_leftovers

    (tmp_path / ".bobignore").write_bytes(
        b"# mine \x97 keep\nbuild/\n"
        b"# >>> cgh guard (managed, do not edit) >>>\n.cgh/\n"
        b"# <<< cgh guard <<<\n"
    )
    assert cleanup_guard_leftovers(tmp_path).kept_rules == 1  # must not raise
    report = cleanup_guard_leftovers(tmp_path, remove_rules=True)
    assert report.bobignore_lines == [".cgh/"]
    txt = (tmp_path / ".bobignore").read_text(encoding="utf-8", errors="replace")
    assert "build/" in txt and "cgh guard" not in txt
