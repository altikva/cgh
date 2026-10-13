# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Every bundled SKILL.md must carry valid YAML frontmatter. An
#              unquoted description containing ": " is a YAML error, and Bob
#              skips such a skill silently ("invalid or unparseable content").

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import codegraph

SKILLS = sorted((Path(codegraph.__file__).parent / "skills").glob("*/SKILL.md"))


@pytest.mark.parametrize("skill", SKILLS, ids=lambda p: p.parent.name)
def test_frontmatter_is_valid_yaml(skill):
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---")
    meta = yaml.safe_load(text.split("---", 2)[1])
    assert meta["name"] == skill.parent.name
    assert isinstance(meta["description"], str) and meta["description"]
