# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: force_index must not reach absolute paths outside the repo root.

from __future__ import annotations

from codegraph.server.tools_index import _within_repo


def test_file_inside_repo(tmp_path):
    (tmp_path / "a").mkdir()
    f = tmp_path / "a" / "x.py"
    f.write_text("pass\n")
    assert _within_repo(f, tmp_path) is True


def test_path_outside_repo_is_refused(tmp_path):
    outside = tmp_path.parent / "elsewhere" / "secret.py"
    assert _within_repo(outside, tmp_path) is False


def test_dotdot_escape_is_refused(tmp_path):
    (tmp_path / "sub").mkdir()
    escape = tmp_path / "sub" / ".." / ".." / "etc-passwd"
    assert _within_repo(escape, tmp_path) is False
