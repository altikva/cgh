# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-05-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Resolve ImportRef.source_module to a file path on disk so
# the indexer can wire IMPORTS edges between File nodes. Filesystem-based
# only, no sys.path traversal. Python absolute imports are tried against
# every plausible source root (package root, src/, repo root, the
# importer's own directory); JS/TS adds tsconfig aliases and workspaces.

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


# Source roots per importer directory. The walk is a handful of stat calls,
# but it runs once per import of every file, so memoise it.
_PY_ROOTS_CACHE: dict[str, tuple[Path, ...]] = {}


def reset_for_tests() -> None:
    """Drop the memoised source roots."""
    _PY_ROOTS_CACHE.clear()
    _JS_ROOTS_CACHE.clear()
    _JAVA_ROOTS_CACHE.clear()
    _RUST_ROOTS_CACHE.clear()
    _GO_MODULE_CACHE.clear()


def _python_source_roots(importer_dir: Path, repo_root: Path) -> tuple[Path, ...]:
    """Directories an absolute Python import may be written against.

    Anchoring only on the repo root misses every layout that does not put
    the top-level package there: `src/` layouts resolve nothing at all, and
    so does a package nested one directory down. The candidates, in order:

      1. the parent of the importer's own top-level package, found by
         walking up while `__init__.py` exists (covers src/ layouts and
         packages nested anywhere),
      2. `<repo>/src`, for a src layout whose packages carry no
         `__init__.py` (namespace packages),
      3. the repo root itself,
      4. the importer's own directory and every directory up to the repo
         root, nearest first: what a script run from its own folder sees,
         and what a service running with its package directory as the
         working directory sees.
    """
    key = str(importer_dir)
    cached = _PY_ROOTS_CACHE.get(key)
    if cached is not None:
        return cached

    package_root = importer_dir
    while (
        package_root / "__init__.py"
    ).is_file() and package_root.parent != package_root:
        package_root = package_root.parent

    # Directories between the importer and the repo root, nearest first. A
    # service whose code lives in `app/` and runs with `app/` as its working
    # directory writes `from providers import x`, and nothing on disk says so.
    ancestors: list[Path] = []
    walker = importer_dir
    while True:
        ancestors.append(walker)
        if walker == repo_root or walker.parent == walker:
            break
        walker = walker.parent

    roots: list[Path] = [package_root, repo_root / "src", repo_root, *ancestors]
    seen: set[str] = set()
    ordered: list[Path] = []
    for root in roots:
        text = str(root)
        if text in seen or not root.is_dir():
            continue
        seen.add(text)
        ordered.append(root)

    result = tuple(ordered)
    _PY_ROOTS_CACHE[key] = result
    return result


def resolve_python(
    source_module: str, importer_path: Path, repo_root: Path
) -> Path | None:
    """
    Resolve a Python import target to a file path.

    ``source_module`` is a dotted module name (``"foo.bar"``) or relative
    (``".foo"``, ``"..bar.baz"``). ``importer_path`` is the file doing
    the import. Returns the absolute path of the target file, or None
    if it can't be resolved (external dependency, virtual env, etc.).

    Best-effort filesystem-only resolution, no sys.path traversal, no
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
        if not parts:
            # `from . import x`, treat as the package's __init__.
            return _try_paths([base / "__init__.py"])
        return _resolve_python_under(base, parts)

    # Absolute import: try each plausible source root, nearest first.
    parts = source_module.split(".")
    for base in _python_source_roots(importer_dir, repo_root.resolve()):
        if hit := _resolve_python_under(base, parts):
            return hit
    return None


def _resolve_python_under(base: Path, parts: list[str]) -> Path | None:
    """Resolve a dotted module below one source root."""
    target_dir = base.joinpath(*parts[:-1]) if len(parts) > 1 else base
    leaf = parts[-1]
    return _try_paths(
        [
            target_dir / f"{leaf}.py",
            target_dir / f"{leaf}.pyi",
            target_dir / leaf / "__init__.py",
        ]
    )


# Project roots per importer directory, for the `~/` and `@/` conventions.
_JS_ROOTS_CACHE: dict[str, tuple[Path, ...]] = {}

# Files that mark the root of a JS/TS project inside a repo.
_JS_PROJECT_MARKERS = (
    "nuxt.config.ts",
    "nuxt.config.js",
    "nuxt.config.mjs",
    "vite.config.ts",
    "vite.config.js",
    "package.json",
)


def _js_source_roots(importer_dir: Path, repo_root: Path) -> tuple[Path, ...]:
    """Directories `~/x` and `@/x` may point at.

    Both mean "the app source root" in Nuxt and Vite, and neither is written
    down anywhere when the alias comes from the framework rather than a
    tsconfig: Nuxt generates its tsconfig into `.nuxt/`, which is a build
    artifact nobody commits. So walk up to the nearest project marker and try
    that directory, its `app/` (Nuxt 4) and its `src/` (Vite), then the same
    three at the repo root.
    """
    key = str(importer_dir)
    cached = _JS_ROOTS_CACHE.get(key)
    if cached is not None:
        return cached

    project = None
    current = importer_dir
    while True:
        if any((current / marker).is_file() for marker in _JS_PROJECT_MARKERS):
            project = current
            break
        if current == repo_root or current.parent == current:
            break
        current = current.parent

    candidates: list[Path] = []
    for base in (project, repo_root):
        if base is None:
            continue
        candidates.extend([base / "app", base / "src", base])

    seen: set[str] = set()
    ordered: list[Path] = []
    for cand in candidates:
        text = str(cand)
        if text in seen or not cand.is_dir():
            continue
        seen.add(text)
        ordered.append(cand)

    result = tuple(ordered)
    _JS_ROOTS_CACHE[key] = result
    return result


def _resolve_target_with_exts(target: Path) -> Path | None:
    """Given a bare path, try common JS/TS extensions and directory index files."""
    if target.is_file():
        return target
    with_ext = [target.with_suffix(ext) for ext in _JS_TS_EXTS]
    if hit := _try_paths(with_ext):
        return hit
    if target.is_dir() and (hit := _try_paths([target / idx for idx in _JS_TS_INDEX])):
        return hit
    return None


def resolve_js_ts(
    source_module: str, importer_path: Path, repo_root: Path
) -> Path | None:
    """
    Resolve a JavaScript / TypeScript import target to a file path.

    Layered resolution:
      1. Relative paths (``"./foo"``, ``"../utils/bar"``)
      2. Absolute paths from the repo root (``"/src/utils"``)
      3. tsconfig.json compilerOptions.paths aliases (``"@/utils"``)
      4. the ``~/`` and ``@/`` framework convention (Nuxt, Vite)
      5. workspace packages (npm, pnpm, yarn)

    Bare specifiers (``"react"``, ``"h3"``, ``"node:crypto"``) return None:
    they are third-party deps, not user code.
    """
    if not source_module:
        return None

    importer = importer_path.resolve()
    importer_dir = importer.parent

    # 1. Relative, anchor at the importer's directory.
    if source_module.startswith("."):
        target = (importer_dir / source_module).resolve()
        return _resolve_target_with_exts(target)

    # 2. Absolute-from-root: '/src/utils' → repo_root + 'src/utils'
    if source_module.startswith("/"):
        target = (repo_root.resolve() / source_module.lstrip("/")).resolve()
        return _resolve_target_with_exts(target)

    # 3. tsconfig path alias
    from codegraph.imports.tsconfig import resolve_alias

    for cand in resolve_alias(source_module, importer_dir):
        if hit := _resolve_target_with_exts(cand):
            return hit

    # 4. Framework convention: `~/x` and `@/x` address the app source root in
    # Nuxt and Vite. `@scope/pkg` is a package, not an alias, so the second
    # character has to be a slash.
    if source_module[:2] in ("~/", "@/"):
        rest = source_module[2:]
        for base in _js_source_roots(importer_dir, repo_root.resolve()):
            if hit := _resolve_target_with_exts(base / rest):
                return hit

    # 5. Workspace package (npm / pnpm / yarn). Imports of the form
    # `@scope/pkg` or `bare-pkg/subpath` resolve to the package's entry
    # point or the named subpath inside the workspace directory.
    from codegraph.imports.workspaces import resolve_workspace_import

    for cand in resolve_workspace_import(source_module, importer_dir):
        if hit := _resolve_target_with_exts(cand):
            return hit

    return None


# Languages with a resolver behind them. Everything else parses its imports
# and then drops them here, which is why a Go or Terraform repo shows zero
# import edges: not a missing edge, a missing resolver.
# Source roots per importer directory, for Java's package-to-path mapping.
_JAVA_ROOTS_CACHE: dict[str, tuple[Path, ...]] = {}

# Directory names that end a Java source root. A package path is written
# against one of these, never against the repo root of a Maven or Gradle
# project, and a multi-module build has one per module.
_JAVA_ROOT_SUFFIXES = (
    ("src", "main", "java"),
    ("src", "test", "java"),
    ("src", "main", "kotlin"),
    ("src",),
)


def _java_source_roots(importer_dir: Path, repo_root: Path) -> tuple[Path, ...]:
    """Directories a Java package path may be written against.

    A package maps to a directory chain, so the root is whichever ancestor
    of the importer ends the conventional prefix: `src/main/java` for a
    Maven or Gradle module, `src/test/java` for its tests, plain `src/` for
    a flat project. A multi-module build has one per module, which is why
    the walk starts at the importer and climbs rather than guessing from
    the repo root.
    """
    key = str(importer_dir)
    cached = _JAVA_ROOTS_CACHE.get(key)
    if cached is not None:
        return cached

    roots: list[Path] = []
    walker = importer_dir
    while True:
        for suffix in _JAVA_ROOT_SUFFIXES:
            if walker.parts[-len(suffix) :] == suffix:
                roots.append(walker)
                break
        if walker == repo_root or walker.parent == walker:
            break
        walker = walker.parent
    roots.append(repo_root)

    seen: set[str] = set()
    ordered: list[Path] = []
    for root in roots:
        marker = str(root)
        if marker not in seen:
            seen.add(marker)
            ordered.append(root)
    result = tuple(ordered)
    _JAVA_ROOTS_CACHE[key] = result
    return result


def resolve_java(
    source_module: str, importer_path: Path, repo_root: Path
) -> Path | None:
    """Resolve a Java import to the file that declares it.

    ``source_module`` is the dotted path tree-sitter hands back for an
    import declaration: `com.google.gson.TypeAdapter` for a plain import,
    `com.google.gson.TypeAdapter.read` for a static member import, and
    `com.google.gson` for a wildcard, because the asterisk is a separate
    node. Trailing segments are dropped one at a time, so a static import
    and a nested class both land on the file that holds the outer type,
    and a wildcard resolves to nothing rather than to a wrong file.
    """
    if not source_module:
        return None

    importer = importer_path.resolve()
    parts = [p for p in source_module.split(".") if p]
    if not parts:
        return None

    for base in _java_source_roots(importer.parent, repo_root.resolve()):
        candidate = list(parts)
        while candidate:
            target_dir = base.joinpath(*candidate[:-1]) if len(candidate) > 1 else base
            hit = _try_paths([target_dir / f"{candidate[-1]}.java"])
            if hit:
                return hit
            candidate.pop()
    return None


# Go: an import is a package directory addressed by the module path from
# go.mod, not a file. The edge has to point at a file, so the package's
# representative source is used.
_GO_MODULE_CACHE: dict[str, str | None] = {}


def _go_module_path(repo_root: Path) -> str | None:
    """The module path declared in go.mod, or None when there is no module."""
    key = str(repo_root)
    if key in _GO_MODULE_CACHE:
        return _GO_MODULE_CACHE[key]
    module = None
    gomod = repo_root / "go.mod"
    try:
        for line in gomod.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("module "):
                module = stripped[len("module ") :].strip()
                break
    except OSError:
        module = None
    _GO_MODULE_CACHE[key] = module
    return module


def _go_package_file(pkg_dir: Path) -> Path | None:
    """One file standing for a package directory, tests last."""
    if not pkg_dir.is_dir():
        return None
    named = pkg_dir / f"{pkg_dir.name}.go"
    if named.is_file():
        return named.resolve()
    sources = sorted(f for f in pkg_dir.glob("*.go") if not f.name.endswith("_test.go"))
    if sources:
        return sources[0].resolve()
    return None


def resolve_go(source_module: str, importer_path: Path, repo_root: Path) -> Path | None:
    """Resolve a Go import to a file inside this module.

    Only the module's own packages resolve: an import is `<module>/<dir>`,
    so anything not carrying the go.mod module prefix is the standard
    library or a dependency and is refused rather than guessed at.
    """
    if not source_module:
        return None
    root = repo_root.resolve()
    module = _go_module_path(root)
    if not module or source_module == module:
        return _go_package_file(root) if source_module == module else None
    prefix = module + "/"
    if not source_module.startswith(prefix):
        return None
    rel = source_module[len(prefix) :].strip("/")
    if not rel:
        return None
    return _go_package_file(root.joinpath(*rel.split("/")))


# Rust: a use path addresses a module, which is a file or a directory with
# mod.rs. `mod` declarations are what actually wire files together, and the
# parser does not emit those yet, so only `use` paths resolve here.
_RUST_ROOTS_CACHE: dict[str, tuple[Path, ...]] = {}


def _rust_crate_roots(importer_dir: Path, repo_root: Path) -> tuple[Path, ...]:
    """Crate source roots above the importer, nearest first."""
    key = str(importer_dir)
    cached = _RUST_ROOTS_CACHE.get(key)
    if cached is not None:
        return cached
    roots: list[Path] = []
    walker = importer_dir
    while True:
        if walker.name == "src":
            roots.append(walker)
        if walker == repo_root or walker.parent == walker:
            break
        walker = walker.parent
    roots.append(repo_root / "src")
    roots.append(repo_root)
    seen: set[str] = set()
    ordered = []
    for r in roots:
        if str(r) not in seen:
            seen.add(str(r))
            ordered.append(r)
    result = tuple(ordered)
    _RUST_ROOTS_CACHE[key] = result
    return result


def _rust_module_file(base: Path, parts: list[str]) -> Path | None:
    """The file holding a module path, trailing item names dropped.

    A use path names a module and then an item inside it, and a grouped
    import expands to one path per item, so `crate::escape::escape_os` has
    to fall back to `escape.rs`. Dropping from the right stops at the first
    file that exists, which is the module that actually holds the item.
    """
    candidate = list(parts)
    while candidate:
        target = base.joinpath(*candidate)
        hit = _try_paths([target.with_suffix(".rs"), target / "mod.rs"])
        if hit:
            return hit
        candidate.pop()
    return None


def _rust_self_dir(importer: Path) -> Path:
    """The directory holding a Rust module's children.

    A crate root and a `mod.rs` own the directory they sit in, so their
    submodules are siblings. Every other file owns a directory named after
    it: `src/foo.rs` declaring `mod bar;` means `src/foo/bar.rs`, never
    `src/bar.rs`. Getting this wrong points a whole module tree one level
    too high.

    Crate roots are not only `lib.rs` and `main.rs`. Cargo compiles every
    file sitting directly in `tests/`, `benches/` and `examples/`, and in
    `src/bin/`, as its own crate, so those are roots too and their modules
    are siblings as well.
    """
    if importer.stem in ("lib", "main", "mod"):
        return importer.parent
    if importer.parent.name in ("tests", "benches", "examples", "bin"):
        return importer.parent
    return importer.parent / importer.stem


def resolve_rust(
    source_module: str, importer_path: Path, repo_root: Path
) -> Path | None:
    """Resolve a Rust `use` path to the file holding that module.

    The parser hands back the raw text of the use tree, so a grouped import
    arrives as `crate::a::{b, c}` and an alias as `serde::Serialize as S`.
    Both are cut back to the path before resolving. `crate::` starts at the
    crate root, `self::` and `super::` walk from the importing file, and a
    bare path is tried against the crate root before being refused, which
    is what keeps external crates out of the graph.
    """
    if not source_module:
        return None
    head = source_module.split("{")[0].split(" as ")[0].strip()
    parts = [p for p in head.split("::") if p and p != "*"]
    if not parts:
        return None

    importer = importer_path.resolve()
    importer_dir = importer.parent
    root = repo_root.resolve()

    if parts[0] in ("self", "super"):
        base = _rust_self_dir(importer)
        rest = parts[:]
        while rest and rest[0] in ("self", "super"):
            if rest[0] == "super":
                base = base.parent
            rest = rest[1:]
        return _rust_module_file(base, rest) if rest else None

    if parts[0] == "crate":
        rest = parts[1:]
        for base in _rust_crate_roots(importer_dir, root):
            if hit := _rust_module_file(base, rest):
                return hit
        return None

    # A bare path is only ours when a matching module exists; otherwise it
    # is an external crate and must not produce an edge.
    for base in _rust_crate_roots(importer_dir, root):
        if hit := _rust_module_file(base, parts):
            return hit
    return None


RESOLVABLE_LANGS = frozenset(
    {"python", "typescript", "tsx", "javascript", "vue", "java", "go", "rust"}
)


def resolve_import(
    lang: str,
    source_module: str,
    importer_path: str | Path,
    repo_root: str | Path,
) -> Path | None:
    """
    Resolve any supported language's import to a file path.

    Returns None when the import can't be resolved, that's the common
    case (third-party deps, virtual env imports, missing files). Callers
    should skip the IMPORTS edge silently for those.
    """
    importer = Path(importer_path)
    root = Path(repo_root)

    if lang == "python":
        return resolve_python(source_module, importer, root)
    if lang in ("typescript", "tsx", "javascript", "vue"):
        return resolve_js_ts(source_module, importer, root)
    if lang == "java":
        return resolve_java(source_module, importer, root)
    if lang == "go":
        return resolve_go(source_module, importer, root)
    if lang == "rust":
        return resolve_rust(source_module, importer, root)
    return None
