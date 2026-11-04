# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-15
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Likely code locations for a task written in prose (a ticket, a
#              sub-agent brief), so an agent starts from file:line instead of
#              exploring the tree. Read-only over the FTS index, three passes,
#              most precise first:
#              1. names written as code in the text (`foo_bar`, CamelCase)
#                 that are exact symbol names;
#              2. file names or paths the text cites, matched against the
#                 files git tracks (YAML and workflows are not in the FTS);
#              3. a prose search on the text's keywords (title first).
#              task_locations returns "path:start-end  kind name" lines.

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

_IDENT = re.compile(
    r"\b(?:[a-z]+(?:_[a-z0-9]+)+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+"
    r"|[a-z]+[A-Z][A-Za-z0-9]+)\b"
)
_CODE_SPAN = re.compile(r"`([^`\n]{2,120})`")
_PATHLIKE = re.compile(r"^[\w./-]+\.[A-Za-z0-9]{1,6}$")
# Generated or data files rank high on any route name and are never where a
# change goes: an openapi.json alone once filled a whole list.
_NOISE = re.compile(r"(\.json$|\.lock$|/generated/|\.d\.ts$|lock\.yaml$)")
_PER_FILE = 4
_MAX_DOCS = 3
_CITED = "file cited in the task"


def task_locations(root: Path, text: str, max_lines: int = 25) -> list[str]:
    """The likely locations of ``text`` in the repo at ``root``, best first.
    Empty when the repo has no FTS index or nothing matches."""
    from codegraph.core.fts import fts_search
    from codegraph.core.utils import ro_sqlite_uri

    root = Path(root).resolve()
    db = root / ".codegraph" / "fts.db"
    if not db.is_file() or not text.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    per_file: dict[str, int] = {}

    def add(path: str, start: int, end: int, label: str) -> None:
        rel = path
        if Path(path).is_absolute():
            try:
                rel = str(Path(path).resolve().relative_to(root))
            except ValueError:
                return  # outside the repo: not this task's code
        cited = label == _CITED
        if (start < 1 or _NOISE.search(rel)) and not cited:
            return
        key = f"{rel}:{start}"
        if key in seen or len(out) >= max_lines or per_file.get(rel, 0) >= _PER_FILE:
            return
        seen.add(key)
        per_file[rel] = per_file.get(rel, 0) + 1
        span = f"{start}-{end}" if end and end > start else str(start)
        out.append(f"{rel}:{span}  {label}")

    conn = sqlite3.connect(ro_sqlite_uri(db), uri=True, timeout=2)
    try:
        spans = _CODE_SPAN.findall(text)
        names: list[str] = []
        for s in spans + _IDENT.findall(text):
            for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", s):
                if n not in names and (_IDENT.fullmatch(n) or s == n):
                    names.append(n)
        for n in names[:15]:
            for r in fts_search(conn, n, limit=5):
                if r.name == n or r.name.endswith("." + n):
                    add(r.file_path, r.start_line, r.end_line, f"{r.kind} {r.name}")

        cited = [s for s in spans if _PATHLIKE.match(s)]
        if cited:
            try:
                files = subprocess.run(
                    ["git", "-C", str(root), "ls-files"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                ).stdout.splitlines()
            except (OSError, subprocess.SubprocessError):
                files = []
            for c in cited[:10]:
                for f in [f for f in files if f == c or f.endswith("/" + c)][:3]:
                    add(f, 1, 0, _CITED)

        # The first line usually names the subject; the rest wanders.
        # Code before docs: the agent edits code, and a prose query ranks
        # Markdown sections high on any shared word.
        title = text.strip().splitlines()[0]
        hits = fts_search(conn, f"{title}\n{text}", limit=max_lines * 2, prose=True)
        docs = [r for r in hits if r.kind == "md_section"]
        for r in [r for r in hits if r.kind != "md_section"] + docs[:_MAX_DOCS]:
            add(r.file_path, r.start_line, r.end_line, f"{r.kind} {r.name}")
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    return out
