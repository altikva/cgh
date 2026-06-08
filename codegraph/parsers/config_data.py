# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
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
) -> None:
    if len(idx.sections) >= _MAX_SECTIONS:
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
    one nested level: GitHub Actions jobs, compose services, k8s spec keys),
    falling back to an indentation scan of column-0 keys when it is not."""

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
            _sections_from_mapping(idx, path_str, data, lines)

        if not seen_any:
            _yaml_top_keys_scan(idx, path_str, lines)
        return idx
