# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Shared utility functions: single source of truth.

from __future__ import annotations

import unicodedata
from pathlib import Path


def rows(result) -> list[dict]:
    """Convert a Kuzu query result to a list of dicts."""
    out: list[dict] = []
    try:
        col_names = result.get_column_names()
        while result.has_next():
            row = result.get_next()
            out.append(dict(zip(col_names, row)))
    except Exception:
        pass
    return out


def short_path(path: str, root: str | Path) -> str:
    """Shorten an absolute path to be relative to *root*."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def normalize_identifier(text: str) -> str:
    """NFKC-normalize an identifier so visually-identical forms collapse.

    Composed (``é`` = U+00E9) and decomposed (``é`` = e + combining
    acute) representations of the same character become byte-identical.
    Fullwidth ``Ｆｏｏ`` collapses to ASCII ``Foo``. Critical for repos
    with non-ASCII identifiers (CJK, accented Latin, Cyrillic, etc.) so
    the graph doesn't fork a single symbol into two nodes.

    Whitespace is preserved (callers strip it as needed); only Unicode
    canonical compatibility composition is applied.

    Ported from graphify's _make_id normalization step.
    """
    if not text:
        return text
    return unicodedata.normalize("NFKC", text)


def safe_id(name: str) -> str:
    """Make a string safe for use as a Mermaid node ID."""
    return (
        name.replace("/", "_")
        .replace(".", "_")
        .replace("-", "_")
        .replace(" ", "_")
        .replace("(", "")
        .replace(")", "")
        .replace("'", "")
        .replace('"', "")
        .replace("`", "")
        .replace(":", "_")
        .replace(",", "")[:60]
    )


def lang_color(suffix: str) -> str:
    """Return a Rich color name for a file extension."""
    return {
        ".py": "green",
        ".ts": "blue",
        ".tsx": "blue",
        ".js": "yellow",
        ".mjs": "yellow",
        ".tf": "magenta",
        ".md": "cyan",
        ".mdx": "cyan",
    }.get(suffix, "white")
