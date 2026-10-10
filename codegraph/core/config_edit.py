# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Line-level edits of one key in one table of .codegraph/config.toml.
#              The file is edited in place so comments, key order and nested
#              tables survive; a missing table is created. Every edit is
#              verified by parsing the result: the target key must read back
#              with the new value and everything else must be unchanged, or the
#              file is left untouched and ConfigEditError is raised. No caller
#              can report success for a write that did not happen.

from __future__ import annotations

import copy
import os
import re
import tomllib
from pathlib import Path
from typing import Any

_TABLE_HEADER = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


class ConfigEditError(ValueError):
    """The config could not be edited safely; the file was not changed."""


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def emit_value(value: Any) -> str:
    """Render a scalar or a flat list as a TOML value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(emit_value(v) for v in value) + "]"
    return f'"{_escape(str(value))}"'


def _bracket_delta(line: str) -> int:
    """Net open brackets on a line, ignoring string contents and comments."""
    depth = 0
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        i += 1
    return depth


def _locate(lines: list[str], table: str, key: str) -> tuple[int, int, int]:
    """Return (header_index, key_start, key_end) for ``key`` in ``[table]``.

    header_index is -1 when the table is absent; key_start is -1 when the key
    is absent. key_end is exclusive and follows a multi-line array to its
    closing bracket."""
    key_re = re.compile(rf"^\s*(?:{re.escape(key)}|\"{re.escape(key)}\")\s*=")
    current = ""
    header_idx = -1
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _TABLE_HEADER.match(line)
        if m:
            current = m.group(2) if m.group(1) == "[" else "[[" + m.group(2)
            if current == table and header_idx == -1:
                header_idx = i
            i += 1
            continue
        if current == table and key_re.match(line):
            value = line.split("=", 1)[1]
            depth = _bracket_delta(value)
            end = i + 1
            while depth > 0 and end < len(lines):
                depth += _bracket_delta(lines[end])
                end += 1
            return header_idx, i, end
        i += 1
    return header_idx, -1, -1


def _commit(path: Path, edited_text: str, expected: dict) -> None:
    try:
        parsed = tomllib.loads(edited_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigEditError(f"edit would make {path} invalid TOML: {exc}") from exc
    if parsed != expected:
        raise ConfigEditError(f"edit of {path} did not read back as intended")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(edited_text, encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path) -> tuple[str, dict]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigEditError(
            f"{path} is not valid TOML, not editing it: {exc}"
        ) from exc


def set_key(path: Path, table: str, key: str, value: Any) -> None:
    """Set ``[table] key = value`` in the TOML file at ``path``.

    Creates the file and the table when missing. Raises ConfigEditError,
    leaving the file untouched, when the result would not parse or would
    not read back with exactly this change."""
    path = Path(path)
    text, original = _load(path)
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    new_line = f"{key} = {emit_value(value)}\n"
    header_idx, start, end = _locate(lines, table, key)
    if start >= 0:
        lines[start:end] = [new_line]
    elif header_idx >= 0:
        lines.insert(header_idx + 1, new_line)
    else:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend([f"[{table}]\n", new_line])

    expected = copy.deepcopy(original)
    expected.setdefault(table, {})[key] = (
        list(value) if isinstance(value, tuple) else value
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _commit(path, "".join(lines), expected)


def remove_key(path: Path, table: str, key: str) -> bool:
    """Remove ``key`` from ``[table]``. Returns False when it was absent."""
    path = Path(path)
    if not path.exists():
        return False
    text, original = _load(path)
    if key not in original.get(table, {}):
        return False
    lines = text.splitlines(keepends=True)
    _, start, end = _locate(lines, table, key)
    if start < 0:
        raise ConfigEditError(f"could not locate {table}.{key} in {path}")
    del lines[start:end]
    expected = copy.deepcopy(original)
    del expected[table][key]
    _commit(path, "".join(lines), expected)
    return True
