# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Config-as-data parsers for JSON / TOML / YAML.
#              Each top-level key (and one nested level) becomes a section in
#              the EXISTING MdSection model, so config files are searchable and
#              show up in the graph without any new node types. JSON uses the
#              stdlib json module, TOML uses tomllib, and YAML uses PyYAML when
#              it is importable, falling back to an indentation scan otherwise.
#              Parsing never raises: a malformed file yields a partial or empty
#              FileIndex built from a best-effort line scan.

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import register_parser
from .base import BaseParser, FileIndex, SectionDef

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MAX_SECTIONS = 500  # guard against pathological configs


def _preview(value: Any) -> str:
    """Short, single-line summary of a config value for the section body."""
    if isinstance(value, dict):
        keys = ", ".join(str(k) for k in list(value)[:10])
        return f"{{{keys}}}" if keys else "{}"
    if isinstance(value, list):
        items = ", ".join(str(v) for v in value[:8] if not isinstance(v, dict | list))
        return f"[{items}]" if items else f"[{len(value)} items]"
    text = str(value)
    return re.sub(r"\s+", " ", text)[:200]


def _add_section(
    idx: FileIndex,
    path_str: str,
    title: str,
    level: int,
    line: int,
    body: str,
    cap: int = _MAX_SECTIONS,
) -> None:
    if len(idx.sections) >= cap:
        return
    sec_id = f"{path_str}::{title}"
    if any(s.id == sec_id for s in idx.sections):
        sec_id = f"{path_str}::{title}-L{line}"
    idx.sections.append(
        SectionDef(
            id=sec_id,
            title=title,
            level=level,
            file_path=path_str,
            start_line=line,
            end_line=line,
            body_preview=body,
            anchor=title,
            kind="config",
        )
    )


def _line_of_key(lines: list[str], key: str) -> int:
    """Best-effort source line for a top-level key (1-based, defaults to 1)."""
    # JSON/TOML/YAML all write the key near the start of its line.
    pat = re.compile(rf"""^\s*['"]?{re.escape(str(key))}['"]?\s*[:=\[]""")
    for i, line in enumerate(lines, start=1):
        if pat.match(line):
            return i
    return 1


def _sections_from_mapping(
    idx: FileIndex,
    path_str: str,
    data: dict,
    lines: list[str],
) -> None:
    """Turn a parsed mapping into sections: every top-level key, plus one
    nested level for dict values (e.g. package.json scripts.<name>)."""
    for key, value in data.items():
        line = _line_of_key(lines, key)
        _add_section(idx, path_str, str(key), 1, line, _preview(value))
        if isinstance(value, dict):
            for sub in list(value)[:50]:
                _add_section(
                    idx,
                    path_str,
                    f"{key}.{sub}",
                    2,
                    _line_of_key(lines, sub),
                    _preview(value[sub]),
                )


# A contracts/ directory holds hand-written registries (env vars, secrets,
# scheduled jobs) that agents search by entry name and by value: one section
# per entry two levels down, its scalar fields as the searchable body.
_CONTRACT_MAX_SECTIONS = 2000
_CONTRACT_BODY_CAP = 400


def _is_contract(path_str: str) -> bool:
    return "contracts" in Path(path_str).parts[:-1]


def _fields_preview(value: Any) -> str:
    """`key: value; ...` over a mapping's scalar and list fields."""
    if not isinstance(value, dict):
        return _preview(value)
    # Stop as soon as the preview is full: YAML aliases can make a small file
    # load as huge shared structures, so never walk or join more than needed.
    parts: list[str] = []
    size = 0
    for k, v in value.items():
        if size > _CONTRACT_BODY_CAP:
            break
        if isinstance(v, dict):
            part = f"{k}: {_preview(v)}"
        elif isinstance(v, list):
            items = []
            for i in v:
                if not isinstance(i, dict | list):
                    items.append(str(i)[:_CONTRACT_BODY_CAP])
                if sum(len(x) for x in items) > _CONTRACT_BODY_CAP:
                    break
            part = f"{k}: [{', '.join(items)}]"
        else:
            part = f"{k}: {str(v)[:_CONTRACT_BODY_CAP]}"
        parts.append(part)
        size += len(part)
    return re.sub(r"\s+", " ", "; ".join(parts))[:_CONTRACT_BODY_CAP]


def _line_after(lines: list[str], key: str, start: int) -> int:
    """Source line of ``key`` at or after line ``start`` (1-based)."""
    pat = re.compile(rf"""^\s*['"]?{re.escape(str(key))}['"]?\s*:""")
    for i in range(max(start, 1), len(lines) + 1):
        if pat.match(lines[i - 1]):
            return i
    return start


def _contract_sections(
    idx: FileIndex, path_str: str, data: dict, lines: list[str]
) -> None:
    """Three levels of sections for a contracts/ YAML file."""
    cap = _CONTRACT_MAX_SECTIONS

    def full() -> bool:
        return len(idx.sections) >= cap

    def add(title: str, level: int, line: int, value: Any) -> None:
        _add_section(idx, path_str, title, level, line, _fields_preview(value), cap)

    # Every loop stops at the section cap: aliases can repeat one large
    # mapping under many keys, and the walk must not grow with the expansion.
    for key, value in data.items():
        if full():
            return
        line = _line_after(lines, key, 1)
        add(str(key), 1, line, value)
        if not isinstance(value, dict):
            continue
        for sub, sub_value in value.items():
            if full():
                return
            sub_line = _line_after(lines, sub, line)
            add(f"{key}.{sub}", 2, sub_line, sub_value)
            if not isinstance(sub_value, dict):
                continue
            for leaf, leaf_value in sub_value.items():
                if full():
                    return
                if isinstance(leaf_value, dict):
                    add(
                        f"{key}.{sub}.{leaf}",
                        3,
                        _line_after(lines, leaf, sub_line),
                        leaf_value,
                    )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


@register_parser(".json", ".jsonc")
class JsonParser(BaseParser):
    """JSON / JSONC config files. Top-level keys (and one nested level) become
    sections, so package.json scripts and tsconfig options stay searchable."""

    lang = "json"
    extensions = [".json", ".jsonc"]
    extracts = ["sections"]
    description = "JSON / JSONC config files"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        idx = FileIndex(path=path_str, lang=self.lang)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx
        lines = text.splitlines()
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            return idx  # malformed JSON, indexed as a bare File node
        if isinstance(data, dict):
            _sections_from_mapping(idx, path_str, data, lines)
        return idx


# ---------------------------------------------------------------------------
# TOML
# ---------------------------------------------------------------------------


@register_parser(".toml")
class TomlParser(BaseParser):
    """TOML config files (pyproject.toml, config.toml). Top tables and one
    nested level become sections."""

    lang = "toml"
    extensions = [".toml"]
    extracts = ["sections"]
    description = "TOML config files"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        idx = FileIndex(path=path_str, lang=self.lang)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx
        lines = text.splitlines()
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError, RecursionError):
            # Fall back to a bracket scan so we still surface [table] headers.
            for i, line in enumerate(lines, start=1):
                m = re.match(r"^\s*\[+([^\]\n]+?)\]*\s*$", line)
                if m and m.group(1).strip():
                    _add_section(idx, path_str, m.group(1).strip(), 1, i, "")
            return idx
        if isinstance(data, dict):
            _sections_from_mapping(idx, path_str, data, lines)
        return idx


# ---------------------------------------------------------------------------
# YAML
# ---------------------------------------------------------------------------

try:  # PyYAML is an installed dependency in this environment, but stay soft.
    import yaml as _yaml
except ImportError:  # pragma: no cover - exercised only without PyYAML
    _yaml = None


def _yaml_top_keys_scan(idx: FileIndex, path_str: str, lines: list[str]) -> None:
    """Indentation-based fallback: column-0 `key:` lines become sections."""
    for i, line in enumerate(lines, start=1):
        if line[:1] in ("#", " ", "\t", "") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z_][\w.\-/]*)\s*:", line)
        if m:
            _add_section(idx, path_str, m.group(1), 1, i, "")


@register_parser(".yaml", ".yml")
class YamlParser(BaseParser):
    """YAML config files. Parses with PyYAML when available (top-level keys plus
    one nested level: GitHub Actions jobs, compose services, k8s spec keys;
    a file under a contracts/ directory gets a third level, each entry with
    its fields as searchable text), falling back to an indentation scan of
    column-0 keys when it is not."""

    lang = "yaml"
    extensions = [".yaml", ".yml"]
    extracts = ["sections"]
    description = "YAML config files"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        idx = FileIndex(path=path_str, lang=self.lang)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx
        lines = text.splitlines()

        if _yaml is None:
            _yaml_top_keys_scan(idx, path_str, lines)
            return idx

        try:
            docs = list(_yaml.safe_load_all(text))
        except Exception:
            # Any YAML error: degrade to the line scan, never raise.
            _yaml_top_keys_scan(idx, path_str, lines)
            return idx

        seen_any = False
        for data in docs:
            if not isinstance(data, dict):
                continue
            seen_any = True
            # k8s manifests: surface kind/metadata.name as a leading section.
            kind = data.get("kind")
            name = None
            meta = data.get("metadata")
            if isinstance(meta, dict):
                name = meta.get("name")
            if isinstance(kind, str) and isinstance(name, str):
                _add_section(
                    idx,
                    path_str,
                    f"{kind}/{name}",
                    1,
                    _line_of_key(lines, "kind"),
                    _preview(data),
                )
            if _is_contract(path_str):
                _contract_sections(idx, path_str, data, lines)
            else:
                _sections_from_mapping(idx, path_str, data, lines)

        if not seen_any:
            _yaml_top_keys_scan(idx, path_str, lines)
        return idx
