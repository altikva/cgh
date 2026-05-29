# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Resolve ImportRef.source_module to a file path on disk so
# the indexer can wire IMPORTS edges between File nodes. Filesystem-based
# only — no tsconfig aliases or workspace packages yet (those come in
# follow-up PRs that build on this).

from __future__ import annotations

from pathlib import Path

# Extensions to try when a JS/TS import has no explicit extension.
_JS_TS_EXTS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")

# Index files to try when an import resolves to a directory.
_JS_TS_INDEX = ("index.ts", "index.tsx", "index.js", "index.jsx", "index.mjs")


def _try_paths(candidates: list[Path]) -> Path | None:
    """First existing file from the candidate list, or None."""
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


def resolve_python(source_module: str, importer_path: Path, repo_root: Path) -> Path | None:
    """
    Resolve a Python import target to a file path.

    ``source_module`` is a dotted module name (``"foo.bar"``) or relative
    (``".foo"``, ``"..bar.baz"``). ``importer_path`` is the file doing
    the import. Returns the absolute path of the target file, or None
    if it can't be resolved (external dependency, virtual env, etc.).

    Best-effort filesystem-only resolution — no sys.path traversal, no
    site-packages lookup. We're modeling the user's repo, not their
    full dependency tree.
    """
    if not source_module:
        return None

    importer = importer_path.resolve()
    importer_dir = importer.parent

    # Relative imports: count leading dots, walk that many directories up.
    leading_dots = 0
    rest = source_module
    while rest.startswith("."):
        leading_dots += 1
        rest = rest[1:]
    if leading_dots > 0:
        base = importer_dir
        for _ in range(leading_dots - 1):
            base = base.parent
        parts = rest.split(".") if rest else []
    else:
        # Absolute import — anchor at the repo root.
        base = repo_root.resolve()
        parts = source_module.split(".")

    if not parts:
        # `from . import x` — treat as the package's __init__.
        return _try_paths([base / "__init__.py"])

    target_dir = base.joinpath(*parts[:-1]) if len(parts) > 1 else base
    leaf = parts[-1]

    return _try_paths(
        [
            target_dir / f"{leaf}.py",
            target_dir / f"{leaf}.pyi",
            target_dir / leaf / "__init__.py",
        ]
    )


def resolve_js_ts(source_module: str, importer_path: Path, repo_root: Path) -> Path | None:
    """
    Resolve a JavaScript / TypeScript import target to a file path.

    Handles relative paths (``"./foo"``, ``"../utils/bar"``) and absolute
    paths from the repo root (``"/src/utils"``). Bare specifiers like
    ``"react"`` return None — they're third-party deps, not user code.
    tsconfig path aliases (``"@/..."``) and workspace packages are
    intentionally NOT handled here; see follow-up PRs.
    """
    if not source_module:
        return None

    # Bare specifier (no leading . or /) — third-party module.
    if not source_module.startswith(".") and not source_module.startswith("/"):
        return None

    importer = importer_path.resolve()
    importer_dir = importer.parent

    if source_module.startswith("/"):
        # Absolute-from-root: '/src/utils' → repo_root + 'src/utils'
        anchor = repo_root.resolve()
        rel = source_module.lstrip("/")
        target = anchor / rel
    else:
        # Relative — anchor at the importer's directory.
        target = (importer_dir / source_module).resolve()

    # If the path already points at a real file, return it.
    if target.is_file():
        return target

    # Try common JS/TS extensions on the bare path.
    with_ext = [target.with_suffix(ext) for ext in _JS_TS_EXTS]
    if hit := _try_paths(with_ext):
        return hit

    # Try treating it as a directory with an index file.
    if target.is_dir():
        if hit := _try_paths([target / idx for idx in _JS_TS_INDEX]):
            return hit

    return None


def resolve_import(
    lang: str,
    source_module: str,
    importer_path: str | Path,
    repo_root: str | Path,
) -> Path | None:
    """
    Resolve any supported language's import to a file path.

    Returns None when the import can't be resolved — that's the common
    case (third-party deps, virtual env imports, missing files). Callers
    should skip the IMPORTS edge silently for those.
    """
    importer = Path(importer_path)
    root = Path(repo_root)

    if lang == "python":
        return resolve_python(source_module, importer, root)
    if lang in ("typescript", "tsx", "javascript", "vue"):
        return resolve_js_ts(source_module, importer, root)
    return None
