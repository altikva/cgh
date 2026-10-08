# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Links to the project point at the altikva/cgh repository. The
#              import name is codegraph, but no altikva/codegraph repository
#              exists, so a link there is dead (seen in the HTML from cgh
#              graph and in the generated config.toml).

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_DEAD = re.compile(r"github\.com[/:]altikva/codegraph\b", re.IGNORECASE)
_SCANNED = ("codegraph", "docs", "packaging", "plugins", "scripts", ".claude-plugin")
_SUFFIXES = {".py", ".md", ".json", ".toml", ".js", ".sh", ".html", ".yml", ".yaml"}


def _files():
    # CHANGELOG is left out: it names the old link when recording the fix.
    for name in ("README.md", "pyproject.toml", "install.sh"):
        path = ROOT / name
        if path.is_file():
            yield path
    for top in _SCANNED:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix in _SUFFIXES and "node_modules" not in path.parts:
                yield path


def test_no_link_points_at_a_codegraph_repository():
    dead = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in _files()
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        )
        if _DEAD.search(line)
    ]
    assert not dead, "links to altikva/codegraph (use altikva/cgh): " + ", ".join(dead)


def test_generated_html_links_to_cgh():
    from codegraph.viz import html

    source = Path(html.__file__).read_text(encoding="utf-8")
    assert 'href="https://github.com/altikva/cgh"' in source
