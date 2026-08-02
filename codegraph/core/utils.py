# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Shared utility functions: single source of truth.

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path


def rows(result) -> list[dict]:
    """Convert a Kuzu query result to a list of dicts.

    Stays resilient (returns whatever rows were read) but no longer fails
    silently: an unexpected error here used to masquerade as an empty result
    and hide query bugs, so it is logged to stderr.
    """
    out: list[dict] = []
    try:
        col_names = result.get_column_names()
        while result.has_next():
            row = result.get_next()
            out.append(dict(zip(col_names, row, strict=False)))
    except Exception as exc:
        print(f"[codegraph] warning: rows() failed: {exc}", file=sys.stderr)
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


def ro_sqlite_uri(db_path: str | Path) -> str:
    """Build a read-only SQLite ``file:`` URI that is valid on every platform.

    A naive ``f"file:{path}?mode=ro"`` breaks on Windows: backslashes are
    literal filename characters in a file URI and a bare ``C:\\...`` is not a
    valid URI path, so the open fails. ``pathname2url`` produces the correct
    ``/C:/...`` form and percent-encodes special characters.
    """
    from urllib.request import pathname2url

    return "file:" + pathname2url(str(db_path)) + "?mode=ro"


def quiet_subprocess_kwargs() -> dict:
    """Keyword arguments that stop a child process from opening a console
    window on Windows. A detached owner has no console of its own, so
    every git.exe it spawns otherwise gets a fresh flashing conhost
    (one per watcher poll on a busy repo). No-op on other platforms.
    """
    import os as _os

    if _os.name == "nt":
        import subprocess as _sp

        return {"creationflags": getattr(_sp, "CREATE_NO_WINDOW", 0)}
    return {}


def is_loopback_url(url: str) -> bool:
    """True only when the URL's host is unambiguously this machine:
    ``localhost``, 127.0.0.0/8 or ``::1``. Egress classification builds
    on this ("local backend" claims must be earned by the URL, not by a
    class attribute), so anything unparsable is NOT loopback: the
    decision fails closed."""
    from urllib.parse import urlsplit

    try:
        host = urlsplit(url if "//" in url else f"//{url}").hostname or ""
    except ValueError:
        return False
    if host.lower() == "localhost":
        return True
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def checked_identifier(name: str) -> str:
    """Allow-list gate for identifiers interpolated into SQL/Cypher
    (field names, order_by entries). Values are always parameterized;
    identifiers cannot be, so anything not identifier-shaped raises
    before it reaches a query string, even though today's callers pass
    constants (defense in depth, per the audit)."""
    if not isinstance(name, str) or not _SQL_IDENT_RE.match(name):
        from codegraph.errors import BackendError

        raise BackendError(f"invalid identifier in query: {name!r}")
    return name
