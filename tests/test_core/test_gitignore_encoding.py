# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh init keeps the whole .cgh/ dir (the auth key included) out
#              of version control with a self-contained .cgh/.gitignore of `*`,
#              never the repo's tracked .gitignore: an init in a worktree must
#              not leave an unrequested diff that blocks its teardown, and the
#              secret is protected with no dependency on a git command. A
#              non-UTF-8 root .gitignore is left exactly as it was.

from __future__ import annotations

import subprocess

from cgh.core.config import init_project
from cgh.state.auth import ensure_state_dir_ignored

# 0x97 is an em dash in CP1252 and an invalid UTF-8 start byte.
_CP1252_GITIGNORE = b"# Landing zone \x97 generated, do not edit\n*.tfstate\n"


def test_init_ignores_the_state_dir_without_touching_the_root_gitignore(tmp_path):
    # No git repo at all: the secret is still protected (no git dependency),
    # and a pre-existing non-UTF-8 root .gitignore is left byte-for-byte.
    (tmp_path / ".gitignore").write_bytes(_CP1252_GITIGNORE)
    before = (tmp_path / ".gitignore").read_bytes()

    init_project(tmp_path)  # must not raise on the bad byte

    assert (tmp_path / ".gitignore").read_bytes() == before  # no unrequested diff
    assert (tmp_path / ".cgh" / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_state_dir_gitignore_actually_ignores_the_auth_key(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    init_project(tmp_path)
    # git honours the .cgh/.gitignore: the key, and the whole dir, are ignored.
    chk = subprocess.run(["git", "check-ignore", "-q", ".cgh/auth.key"], cwd=tmp_path)
    assert chk.returncode == 0


def test_ensure_state_dir_ignored_is_idempotent(tmp_path):
    (tmp_path / ".cgh").mkdir()
    assert ensure_state_dir_ignored(tmp_path) is True
    assert ensure_state_dir_ignored(tmp_path) is False  # already `*`


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


def test_a_negation_line_is_rewritten_away(tmp_path):
    # A planted `!auth.key` must not survive: the file is forced back to `*`.
    cg = tmp_path / ".cgh"
    cg.mkdir()
    (cg / ".gitignore").write_text("*\n!auth.key\n", encoding="utf-8")
    assert ensure_state_dir_ignored(tmp_path) is True
    assert (cg / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_a_symlinked_ignore_file_is_refused(tmp_path):
    # A planted symlink must not be followed into an arbitrary file.
    cg = tmp_path / ".cgh"
    cg.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n", encoding="utf-8")
    (cg / ".gitignore").symlink_to(victim)
    assert ensure_state_dir_ignored(tmp_path) is False
    assert victim.read_text(encoding="utf-8") == "precious\n"  # untouched


def test_a_symlinked_state_dir_is_refused(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".cgh").symlink_to(elsewhere, target_is_directory=True)
    assert ensure_state_dir_ignored(tmp_path) is False
    assert not (elsewhere / ".gitignore").exists()  # nothing written through it


def test_init_refuses_a_symlinked_state_dir(tmp_path):
    import pytest

    from cgh.errors import ConfigurationError

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".cgh").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ConfigurationError):
        init_project(tmp_path)
    # No state (and no auth key) was written through the symlink.
    assert not (elsewhere / "auth.key").exists()
    assert not (elsewhere / "config.toml").exists()


def test_save_auth_key_refuses_a_symlinked_key_file(tmp_path):
    import pytest

    from cgh.errors import ConfigurationError
    from cgh.state.auth import save_auth_key

    cg = tmp_path / ".cgh"
    cg.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n", encoding="utf-8")
    (cg / "auth.key").symlink_to(victim)
    with pytest.raises(ConfigurationError):
        save_auth_key(tmp_path)
    assert victim.read_text(encoding="utf-8") == "precious\n"  # untouched
