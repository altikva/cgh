# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-06
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh init must not crash on a non-UTF-8 .gitignore. A CP1252
#              byte (e.g. an em dash copied from a doc) in a template
#              .gitignore crashed init across every federated subrepo with
#              UnicodeDecodeError; the ignore reads now decode leniently.

from __future__ import annotations

from codegraph.core.config import init_project
from codegraph.state.auth import ensure_gitignore_has_auth_key

# 0x97 is an em dash in CP1252 and an invalid UTF-8 start byte.
_CP1252_GITIGNORE = b"# Landing zone \x97 generated, do not edit\n*.tfstate\n"


def test_init_project_tolerates_non_utf8_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_bytes(_CP1252_GITIGNORE)
    # Must not raise UnicodeDecodeError.
    init_project(tmp_path)
    content = (tmp_path / ".gitignore").read_bytes().decode("utf-8", "replace")
    assert ".codegraph" in content  # cgh entry appended despite the bad byte


def test_ensure_gitignore_auth_key_tolerates_non_utf8(tmp_path):
    (tmp_path / ".gitignore").write_bytes(_CP1252_GITIGNORE)
    ensure_gitignore_has_auth_key(tmp_path)  # must not raise
    content = (tmp_path / ".gitignore").read_bytes().decode("utf-8", "replace")
    assert "auth.key" in content


def test_guard_cleanup_tolerates_non_utf8_bobignore(tmp_path):
    """cgh init counts, and `cgh guard --remove-rules` removes, the managed
    .bobignore block older versions wrote; a CP1252 byte in the user's part
    must not crash either read."""
    from codegraph.state.guard import cleanup_guard_leftovers

    (tmp_path / ".bobignore").write_bytes(
        b"# mine \x97 keep\nbuild/\n"
        b"# >>> cgh guard (managed, do not edit) >>>\n.codegraph/\n"
        b"# <<< cgh guard <<<\n"
    )
    assert cleanup_guard_leftovers(tmp_path).kept_rules == 1  # must not raise
    report = cleanup_guard_leftovers(tmp_path, remove_rules=True)
    assert report.bobignore_lines == [".codegraph/"]
    txt = (tmp_path / ".bobignore").read_text(encoding="utf-8", errors="replace")
    assert "build/" in txt and "cgh guard" not in txt
