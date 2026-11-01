# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Grep fallback shared by the index-based search tools
#              (symbol_lookup, search_symbols, fts_search). When the graph or
#              the FTS index models no match for a name, it may still live in a
#              comment, a string, a config value or a file type the index does
#              not parse. A bounded word-boundary grep returns those hits so an
#              answer is grep-complete in one call instead of sending the agent
#              back to the shell.

from __future__ import annotations

import re
from pathlib import Path

_IDENTIFIER = re.compile(r"[A-Za-z_]\w*")


def grep_term(root: Path | None, term: str, limit: int = 8) -> list[dict]:
    """A bounded word-boundary grep for ``term`` over ``root`` and its
    federated children, as compact ``{scope, file, line, text}`` rows, most at
    ``limit`` in all. Empty unless ``term`` is a single plain identifier, so a
    sentence or a multi-word query is never grepped. The underlying
    pattern_search keeps the scan linear and respects .gitignore."""
    if root is None or not term or not _IDENTIFIER.fullmatch(term):
        return []
    from codegraph.analysis.federation import resolve_children
    from codegraph.analysis.pattern import pattern_search as _search

    pattern = rf"\b{re.escape(term)}\b"
    roots = [root, *(resolve_children(root) or [])]
    out: list[dict] = []
    for scope_root in roots:
        scope = "parent" if scope_root is root else scope_root.name
        try:
            hits, _ = _search(
                scope_root, pattern=pattern, max_results=limit, regex=True
            )
        except Exception:
            continue
        out += [
            {"scope": scope, "file": h.file, "line": h.line, "text": h.text}
            for h in hits
        ]
        if len(out) >= limit:
            break
    return out[:limit]
