# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Resolve JS / TS workspace packages (npm, pnpm, yarn) so the
# indexer can wire IMPORTS edges across monorepo boundaries.

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

# Cache the package-name → directory map per workspace root so we don't
# re-walk the workspace globs on every TS file in the same repo.
_WORKSPACE_CACHE: dict[str, dict[str, Path]] = {}


def _read_json(path: Path) -> dict:
    """Best-effort JSON read; returns {} on any failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _find_workspace_root(start_dir: Path) -> Path | None:
    """Walk up from start_dir looking for the nearest workspace marker.

    Returns the directory containing pnpm-workspace.yaml, a package.json
    with a "workspaces" field, or a lerna.json. Returns None if none is
    found before the filesystem root.
    """
    start_dir = start_dir.resolve()
    for d in [start_dir, *start_dir.parents]:
        if (d / "pnpm-workspace.yaml").is_file():
            return d
        if (d / "lerna.json").is_file():
            return d
        pkg = d / "package.json"
        if pkg.is_file():
            data = _read_json(pkg)
            if "workspaces" in data:
                return d
    return None


def _parse_yaml_packages(text: str) -> list[str]:
    """Pull the `packages` glob list out of pnpm-workspace.yaml.

    We avoid a pyyaml dep, pnpm-workspace.yaml is a tiny YAML
    file with a single root key we can scrape with primitive line parsing.
    Handles list form (``packages:\\n  - apps/*``) and flow form
    (``packages: ['apps/*', 'libs/*']``).
    """
    packages: list[str] = []
    in_packages = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()

        if not in_packages:
            if stripped.startswith("packages:"):
                in_packages = True
                tail = stripped[len("packages:") :].strip()
                if tail.startswith("[") and tail.endswith("]"):
                    inner = tail[1:-1]
                    for tok in inner.split(","):
                        tok = tok.strip().strip("\"'")
                        if tok:
                            packages.append(tok)
                    in_packages = False
            continue

        # Inside the `packages:` block.
        # New top-level key (no leading space) closes the block.
        if not raw_line.startswith((" ", "\t")):
            in_packages = False
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            # Strip trailing `# comment` if outside a quoted string
            if not item.startswith(("\"", "'")):
                hash_pos = item.find("#")
                if hash_pos >= 0:
                    item = item[:hash_pos].strip()
            item = item.strip("\"'")
            if item:
                packages.append(item)
        elif stripped == "-":
            continue
    return packages


def _workspace_globs(root: Path) -> list[str]:
    """Return the workspace glob patterns from whichever marker file exists."""
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        try:
            return _parse_yaml_packages(pnpm.read_text(encoding="utf-8"))
        except OSError:
            return []

    lerna = root / "lerna.json"
    if lerna.is_file():
        data = _read_json(lerna)
        pkgs = data.get("packages")
        if isinstance(pkgs, list):
            return [p for p in pkgs if isinstance(p, str)]

    pkg = root / "package.json"
    if pkg.is_file():
        data = _read_json(pkg)
        ws = data.get("workspaces")
        if isinstance(ws, list):
            return [p for p in ws if isinstance(p, str)]
        if isinstance(ws, dict):
            # yarn extended form: { "packages": [...], "nohoist": [...] }
            pkgs = ws.get("packages")
            if isinstance(pkgs, list):
                return [p for p in pkgs if isinstance(p, str)]
    return []


def _match_glob_dirs(root: Path, pattern: str) -> list[Path]:
    """Materialize a workspace glob pattern into actual directories.

    Patterns like ``"apps/*"`` resolve via Path.glob; ``"packages/**"``
    also works. Negation patterns (``"!apps/dist"``) are honored.
    """
    if pattern.startswith("!"):
        return []
    matched: list[Path] = []
    try:
        for p in root.glob(pattern):
            if p.is_dir():
                matched.append(p)
    except (OSError, ValueError):
        pass
    return matched


def _build_package_map(root: Path) -> dict[str, Path]:
    """For each workspace package under `root`, return {package_name: package_dir}.

    Reads each candidate's package.json and uses the "name" field as the
    key. Packages with missing or unnamed package.json are skipped.
    """
    globs = _workspace_globs(root)
    excludes = [p[1:] for p in globs if p.startswith("!")]
    includes = [p for p in globs if not p.startswith("!")]

    out: dict[str, Path] = {}
    for pattern in includes:
        for d in _match_glob_dirs(root, pattern):
            # Apply excludes
            rel = d.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, ex) for ex in excludes):
                continue
            pkg_json = d / "package.json"
            if not pkg_json.is_file():
                continue
            data = _read_json(pkg_json)
            name = data.get("name")
            if isinstance(name, str) and name:
                out[name] = d
    return out


def load_packages(start_dir: Path) -> dict[str, Path]:
    """Return the workspace package map for the workspace containing
    `start_dir`. Returns an empty dict when there is no workspace root.
    Memoized per workspace root.
    """
    root = _find_workspace_root(start_dir)
    if root is None:
        return {}
    key = str(root.resolve())
    if key not in _WORKSPACE_CACHE:
        _WORKSPACE_CACHE[key] = _build_package_map(root)
    return _WORKSPACE_CACHE[key]


def resolve_workspace_import(raw_import: str, importer_dir: Path) -> list[Path]:
    """
    If `raw_import` starts with a known workspace package name, return
    candidate file paths inside that package. The caller layers JS/TS
    extension / index-file resolution on top.

    Empty list when no workspace exists or the import doesn't match a
    known package name.
    """
    packages = load_packages(importer_dir)
    if not packages:
        return []

    # Try exact match first, then prefix-with-slash for subpath imports.
    if raw_import in packages:
        return _entry_candidates(packages[raw_import], "")

    for name, pkg_dir in packages.items():
        prefix = name + "/"
        if raw_import.startswith(prefix):
            sub = raw_import[len(prefix) :]
            return _entry_candidates(pkg_dir, sub)
    return []


def _entry_candidates(pkg_dir: Path, subpath: str) -> list[Path]:
    """Compute candidate file paths for `pkg_dir/subpath`.

    For a bare package import (subpath = "") this consults package.json's
    "main" / "module" / "exports" fields (string form only, full
    subpath-exports parsing is out of scope) and falls back to standard
    src/index files. For a subpath import it returns the joined path as-is
    (caller adds extensions).
    """
    if subpath:
        return [pkg_dir / subpath]

    candidates: list[Path] = []
    pkg_json = pkg_dir / "package.json"
    if pkg_json.is_file():
        data = _read_json(pkg_json)
        for key in ("module", "main"):
            entry = data.get(key)
            if isinstance(entry, str) and entry:
                candidates.append(pkg_dir / entry)
        exports = data.get("exports")
        if isinstance(exports, str):
            candidates.append(pkg_dir / exports)
        elif isinstance(exports, dict):
            # exports["."] is the bare-import entry point
            root_entry = exports.get(".")
            if isinstance(root_entry, str):
                candidates.append(pkg_dir / root_entry)
            elif isinstance(root_entry, dict):
                # Conditional exports: try "default", "import", "require"
                for key in ("default", "import", "require"):
                    v = root_entry.get(key)
                    if isinstance(v, str):
                        candidates.append(pkg_dir / v)
                        break

    # Standard fallbacks
    candidates.extend(
        [
            pkg_dir / "src" / "index.ts",
            pkg_dir / "src" / "index.tsx",
            pkg_dir / "src" / "index.js",
            pkg_dir / "index.ts",
            pkg_dir / "index.tsx",
            pkg_dir / "index.js",
        ]
    )
    return candidates


def _clear_cache() -> None:
    """Test hook, reset the per-workspace cache."""
    _WORKSPACE_CACHE.clear()
