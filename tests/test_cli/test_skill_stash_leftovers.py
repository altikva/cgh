# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Windows upgrade leftovers: pip and uv stash a directory they
#              replace as "~" plus the name minus its first letter and can
#              leave it behind. Such a "~gh-x" folder is no bundled skill, and
#              the copies an older cgh installed from it are cleaned up.

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from codegraph.integrations import skill_installer as si


@pytest.fixture
def skills_src(tmp_path, monkeypatch) -> Path:
    src = tmp_path / "skills"
    real = Path(si.__file__).parent.parent / "skills"
    shutil.copytree(real / "cgh-papercuts", src / "cgh-papercuts")
    # The stash pip leaves on Windows: same content, mangled name.
    shutil.copytree(real / "cgh-papercuts", src / "~gh-papercuts")
    monkeypatch.setattr(si, "_skills_source_dir", lambda: src)
    return src


def test_stash_leftover_is_not_a_skill(skills_src):
    assert si.list_bundled_skills() == ["cgh-papercuts"]
    assert [name for name, *_ in si._iter_skills()] == ["cgh-papercuts"]


def test_install_claude_skips_and_cleans_stash_copies(skills_src, tmp_path):
    project = tmp_path / "proj"
    old = project / ".claude" / "skills" / "~gh-papercuts"
    shutil.copytree(skills_src / "~gh-papercuts", old)
    mine = project / ".claude" / "skills" / "~gh-notes"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: my-notes\n---\nmine\n")

    assert si.install_claude(project) == ["cgh-papercuts"]
    skills = project / ".claude" / "skills"
    assert (skills / "cgh-papercuts" / "SKILL.md").is_file()
    assert not old.exists()
    # A "~" folder that does not hold the matching skill is the user's.
    assert mine.exists()


def test_install_cursor_drops_stash_rule(skills_src, tmp_path):
    project = tmp_path / "proj"
    rules = project / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "~gh-papercuts.mdc").write_text("stale")

    assert si.install_cursor(project) == ["cgh-papercuts"]
    assert sorted(p.name for p in rules.iterdir()) == ["cgh-papercuts.mdc"]
