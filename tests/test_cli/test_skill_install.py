# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The bundled skills are actually found and installed. Regression
#              guard for the source-dir path (skills live at codegraph/skills,
#              not codegraph/integrations/skills): a wrong path silently
#              installed zero skills for every tool.

from __future__ import annotations

from codegraph.integrations.skill_installer import (
    _iter_skills,
    _skills_source_dir,
    install_bob,
    install_claude,
    install_cursor,
)


def test_source_dir_exists_and_has_skills():
    src = _skills_source_dir()
    assert src.exists(), f"skills source dir missing: {src}"
    skills = _iter_skills()
    assert len(skills) >= 5, "expected the bundled cgh-* skills to be found"
    assert any(name.startswith("cgh-") for name, *_ in skills)


def test_install_populates_skill_dirs(tmp_path):
    installed = install_bob(tmp_path)
    assert installed, "install_bob returned no skills"
    dest = tmp_path / ".bob" / "skills"
    assert dest.is_dir() and any(dest.iterdir())
    # A SKILL.md landed for at least one skill.
    assert list(dest.glob("*/SKILL.md"))


def test_claude_and_cursor_install_skills(tmp_path):
    install_claude(tmp_path / "c")
    assert list((tmp_path / "c" / ".claude" / "skills").glob("*/SKILL.md"))
    install_cursor(tmp_path / "u")
    # Cursor renders skills as .mdc rules; just assert something was written.
    cursor_root = tmp_path / "u" / ".cursor"
    assert cursor_root.exists() and any(cursor_root.rglob("*"))
