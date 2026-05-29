# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Resolve TypeScript path aliases from tsconfig.json so the
# indexer can wire IMPORTS edges through patterns like `@/utils` -> src/utils.

from __future__ import annotations

import json
import re
from pathlib import Path

# Cache the resolved alias map per `tsconfig.json` directory so we don't
# re-parse on every TS file in the same package.
_ALIAS_CACHE: dict[str, dict[str, list[str]]] = {}


def _strip_jsonc(text: str) -> str:
    """Strip // line comments, /* */ block comments, and trailing commas
    from a TS-flavoured JSON-with-comments string so json.loads accepts it.

    Conservative: preserves comment-looking content inside string literals.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    string_char = ""
    while i < n:
        c = text[i]

        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == string_char:
                in_string = False
            i += 1
            continue

        if c in ('"', "'"):
            in_string = True
            string_char = c
            out.append(c)
            i += 1
            continue

        # Line comment
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue

        # Block comment
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue

        out.append(c)
        i += 1

    # Strip trailing commas in objects and arrays
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _read_tsconfig(tsconfig: Path, seen: set[Path] | None = None) -> dict:
    """Parse a tsconfig.json file (JSONC) following any `extends` chain.

    Aliases / baseUrl from extended configs are merged in; the child config
    wins on overlap. Cycles in `extends` are guarded via the `seen` set.
    Returns the merged `compilerOptions` dict (empty if anything fails).
    """
    if seen is None:
        seen = set()
    tsconfig = tsconfig.resolve()
    if tsconfig in seen:
        return {}
    seen.add(tsconfig)

    if not tsconfig.is_file():
        return {}
    try:
        raw = tsconfig.read_text(encoding="utf-8")
        data = json.loads(_strip_jsonc(raw))
    except (OSError, json.JSONDecodeError):
        return {}

    merged: dict = {}
    extends = data.get("extends")
    if isinstance(extends, str):
        # `extends` resolves like a bare import would — supports relative
        # path or a path-aliased / node_modules-style ref. We only handle
        # relative for now (the common case).
        if extends.startswith(".") or extends.startswith("/"):
            # `extends` can be:
            #   - "./tsconfig.base"      → ./tsconfig.base.json
            #   - "./tsconfig.base.json" → use as-is
            #   - "./configs/base"       → ./configs/base/tsconfig.json (dir case)
            if extends.endswith(".json"):
                ext_path = tsconfig.parent / extends
            else:
                cand = tsconfig.parent / (extends + ".json")
                if cand.is_file():
                    ext_path = cand
                else:
                    ext_path = tsconfig.parent / extends / "tsconfig.json"
            parent = _read_tsconfig(ext_path, seen)
            merged.update(parent)

    co = data.get("compilerOptions") or {}
    if isinstance(co, dict):
        # baseUrl is relative to the tsconfig containing it
        if "baseUrl" in co:
            merged["baseUrl"] = str((tsconfig.parent / co["baseUrl"]).resolve())
        if "paths" in co and isinstance(co["paths"], dict):
            existing = merged.get("paths", {})
            existing.update(co["paths"])
            merged["paths"] = existing
        # Carry through tsconfig dir so callers can anchor relative aliases
    merged.setdefault("_tsconfig_dir", str(tsconfig.parent))
    return merged


def load_aliases(start_dir: Path) -> dict:
    """Find the nearest tsconfig.json walking up from `start_dir`, return
    its merged compilerOptions (with baseUrl and paths). Memoized per
    tsconfig directory.

    Returns an empty dict when there's no tsconfig in any parent.
    """
    start_dir = start_dir.resolve()
    for d in [start_dir, *start_dir.parents]:
        tsconfig = d / "tsconfig.json"
        if tsconfig.is_file():
            key = str(d)
            if key not in _ALIAS_CACHE:
                _ALIAS_CACHE[key] = _read_tsconfig(tsconfig)
            return _ALIAS_CACHE[key]
    return {}


def resolve_alias(raw_import: str, importer_dir: Path) -> list[Path]:
    """
    Resolve a TS import string against the nearest tsconfig.json's
    compilerOptions.paths. Returns the list of candidate file paths to
    try (without extension — caller layers JS/TS extensions on top).

    Empty list when no tsconfig is reachable or no alias pattern matches.
    """
    config = load_aliases(importer_dir)
    paths = config.get("paths") or {}
    if not paths:
        return []

    base_url = config.get("baseUrl") or config.get("_tsconfig_dir") or str(importer_dir)
    base = Path(base_url)

    candidates: list[Path] = []
    for pattern, targets in paths.items():
        if not isinstance(targets, list):
            continue
        # Glob-style "@/*" matches "@/foo/bar" with captured="foo/bar"
        if pattern.endswith("/*") and raw_import.startswith(pattern[:-1]):
            captured = raw_import[len(pattern) - 1 :]
            for t in targets:
                if isinstance(t, str):
                    sub = t[:-1] if t.endswith("*") else t
                    candidates.append(base / (sub + captured))
        elif pattern == raw_import:
            for t in targets:
                if isinstance(t, str):
                    candidates.append(base / t)
    return candidates


def _clear_cache() -> None:
    """Test hook — reset the per-directory alias cache."""
    _ALIAS_CACHE.clear()
