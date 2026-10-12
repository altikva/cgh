# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Claude project-dir slug must match Claude Code on every
#              platform. The Windows case is pinned to a real directory name
#              observed on disk.

from __future__ import annotations

from codegraph.core.config import _claude_project_slug_from_abs


def test_posix_path():
    assert (
        _claude_project_slug_from_abs("/Users/joy/IdeaProjects/cgh")
        == "-Users-joy-IdeaProjects-cgh"
    )


def test_windows_path_matches_real_claude_dir():
    # Claude Code names the project dir of C:\Users\dev\IdeaProjects\example
    # ~/.claude/projects/C--Users-dev-IdeaProjects-example
    win = "C:\\Users\\dev\\IdeaProjects\\example"
    assert _claude_project_slug_from_abs(win) == "C--Users-dev-IdeaProjects-example"


def test_windows_drive_colon_and_backslashes_both_become_dash():
    assert _claude_project_slug_from_abs("D:\\a\\b") == "D--a-b"
