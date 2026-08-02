# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: SQL / migration DDL parser. Scans CREATE TABLE and
#              ALTER TABLE ... ADD COLUMN statements with regex (DDL only, no
#              attempt at arbitrary queries) and represents each table as a
#              section titled `table:<name>`, listing its columns in the body
#              preview. Reuses the existing MdSection model, so no new graph
#              node types or schema changes are needed. Parsing never raises.

from __future__ import annotations

import re
from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, SectionDef

# ---------------------------------------------------------------------------
# Regex patterns (DDL only)
# ---------------------------------------------------------------------------

# CREATE TABLE [IF NOT EXISTS] [schema.]name (   -- captures the bare table name
_CREATE_TABLE = re.compile(
    r"""CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?
        (?P<name>[`"\[]?[\w.]+[`"\]]?)
        \s*\(""",
    re.IGNORECASE | re.VERBOSE,
)

# ALTER TABLE [schema.]name ADD [COLUMN] colname coltype
_ALTER_ADD = re.compile(
    r"""ALTER\s+TABLE\s+(?P<name>[`"\[]?[\w.]+[`"\]]?)\s+
        ADD\s+(?:COLUMN\s+)?(?P<col>[`"\[]?\w+[`"\]]?)""",
    re.IGNORECASE | re.VERBOSE,
)

# Names of table-level constraints that are not columns.
_CONSTRAINT_KW = {
    "primary",
    "foreign",
    "unique",
    "constraint",
    "check",
    "key",
    "index",
}


def _clean(name: str) -> str:
    """Strip quoting/backticks/brackets and a schema prefix off an identifier."""
    name = name.strip().strip('`"[]')
    return name.split(".")[-1]


def _split_columns(body: str) -> list[str]:
    """Split the parenthesised body of a CREATE TABLE into top-level column
    definitions, respecting nested parens (e.g. NUMERIC(10, 2))."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _column_names(body: str) -> list[str]:
    """Pull column names out of a CREATE TABLE body, skipping constraints."""
    cols: list[str] = []
    for part in _split_columns(body):
        first = part.split(None, 1)
        if not first:
            continue
        token = first[0]
        if token.strip('`"[]').lower() in _CONSTRAINT_KW:
            continue
        name = _clean(token)
        if name and re.match(r"^\w+$", name):
            cols.append(name)
    return cols


def _matching_paren(text: str, open_idx: int) -> int:
    """Index of the ) that closes the ( at *open_idx*, or len(text) on failure."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


@register_parser(".sql")
class SqlParser(BaseParser):
    """SQL DDL parser. CREATE TABLE -> a `table:<name>` section listing its
    columns; ALTER TABLE ... ADD COLUMN folds extra columns into that table."""

    lang = "sql"
    extensions = [".sql"]
    extracts = ["sections"]
    description = "SQL DDL (CREATE / ALTER TABLE)"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        idx = FileIndex(path=path_str, lang=self.lang)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx

        # table name -> (start_line, [columns])
        tables: dict[str, tuple[int, list[str]]] = {}
        order: list[str] = []

        for m in _CREATE_TABLE.finditer(text):
            name = _clean(m.group("name"))
            if not name:
                continue
            line = text[: m.start()].count("\n") + 1
            open_idx = m.end() - 1  # the "(" matched at the end of the regex
            close_idx = _matching_paren(text, open_idx)
            body = text[open_idx + 1 : close_idx]
            cols = _column_names(body)
            if name in tables:
                # Same table redefined; merge columns, keep first line.
                existing_line, existing_cols = tables[name]
                merged = list(dict.fromkeys(existing_cols + cols))
                tables[name] = (existing_line, merged)
            else:
                tables[name] = (line, cols)
                order.append(name)

        for m in _ALTER_ADD.finditer(text):
            name = _clean(m.group("name"))
            col = _clean(m.group("col"))
            if not name or not col:
                continue
            line = text[: m.start()].count("\n") + 1
            if name in tables:
                start_line, cols = tables[name]
                if col not in cols:
                    cols.append(col)
                tables[name] = (start_line, cols)
            else:
                tables[name] = (line, [col])
                order.append(name)

        for name in order:
            start_line, cols = tables[name]
            preview = "columns: " + ", ".join(cols) if cols else "columns: (none)"
            idx.sections.append(
                SectionDef(
                    id=f"{path_str}::table:{name}",
                    title=f"table:{name}",
                    level=1,
                    file_path=path_str,
                    start_line=start_line,
                    end_line=start_line,
                    body_preview=preview[:300],
                    anchor=name,
                )
            )

        return idx
