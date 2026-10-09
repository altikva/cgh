# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Which functions a by-name call site links to. The parser
#              records each call's shape (bare f(), self.f(), module.f(),
#              obj.f()) and the import binding its name or receiver comes
#              from; site_rows turns that into the stored call_site row and
#              targets_for picks the CALLS targets among the functions that
#              carry the called name. targets_for only reads the site row,
#              the candidate functions and the files the caller imports, so
#              the indexer can recompute it from either end of the edge and
#              the graph stays independent of the order files are indexed in.

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from codegraph.parsers.base import CallRef, FileIndex, SymbolDef

# Site kinds stored in call_site.kind. "" is a site without shape (languages
# whose parser records no call shapes): linked by name alone.
BARE = "bare"  # f()
SELF = "self"  # self.f(), cls.f(), this.f(), super().f(); ctx "Class:Base1,Base2"
MOD = "mod"  # m.f() where m is an imported module; hint = its file
CLS = "cls"  # C.f() where C is a name imported from a repo file; ctx = "C"
ATTR = "attr"  # obj.f() on a receiver of unknown type

# call_site.hint for an import that does not resolve to a repo file: a
# third-party or standard library module, so the call has no target here.
EXTERNAL = "!"

# An attribute call on a receiver of unknown type links to every method of
# that name only when there are at most this many in the repo (outside the
# files the caller imports, which always win). Above it the name is too
# common to guess: get, run, commit, register...
MAX_METHOD_FANOUT = 3

# Methods every codebase calls on objects of other libraries: builtin
# containers and strings, files and paths, DB sessions and cursors, HTTP
# clients, loggers, JS arrays, maps and promises. ``session.add(x)`` or
# ``data.get(k)`` says nothing about which class of the repo is meant, so on
# a receiver of unknown type these link only to a method of a file the caller
# imports, never to "the few methods of that name" in the repo.
COMMON_METHODS = frozenset(
    {
        # dict, list, set, tuple
        *("get", "items", "keys", "values", "update", "pop", "popitem"),
        *("setdefault", "copy", "clear", "append", "extend", "insert", "remove"),
        *("index", "count", "sort", "reverse", "add", "discard", "union"),
        *("intersection", "difference", "issubset", "issuperset"),
        # str, bytes
        *("join", "split", "rsplit", "strip", "lstrip", "rstrip", "replace"),
        *("format", "encode", "decode", "startswith", "endswith", "lower"),
        *("upper", "title", "find", "rfind", "splitlines", "partition"),
        # files, paths, io
        *("read", "write", "close", "open", "readline", "readlines", "flush"),
        *("seek", "exists", "mkdir", "unlink", "read_text", "write_text"),
        *("read_bytes", "write_bytes", "iterdir", "glob", "rglob", "resolve"),
        *("relative_to", "with_suffix", "is_file", "is_dir", "stat", "touch"),
        # DB-API, SQLAlchemy sessions and queries
        *("execute", "executemany", "commit", "rollback", "fetchone"),
        *("fetchall", "fetchmany", "cursor", "scalar", "scalars", "scalar_one"),
        *("scalar_one_or_none", "first", "one", "one_or_none", "all"),
        *("refresh", "merge", "delete", "begin", "connect", "query", "filter"),
        *("filter_by", "where", "order_by", "limit", "offset", "group_by"),
        *("returning", "unique", "expunge", "begin_nested"),
        # HTTP clients, futures, threads, queues
        *("post", "put", "patch", "request", "send", "json", "raise_for_status"),
        *("wait", "cancel", "result", "done", "set_result", "put_nowait"),
        *("get_nowait", "start", "stop", "run", "is_alive", "text"),
        # logging
        *("debug", "info", "warning", "warn", "error", "exception", "critical"),
        *("log",),
        # JS arrays, maps, promises, events
        *("push", "shift", "unshift", "slice", "splice", "map", "reduce"),
        *("forEach", "findIndex", "includes", "indexOf", "some", "every"),
        *("concat", "entries", "then", "catch", "finally", "toString", "trim"),
        *("toLowerCase", "toUpperCase", "startsWith", "endsWith", "has", "set"),
        *("emit", "on", "off", "once"),
    }
)

_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "e2e", "cypress"})
_TEST_SUFFIXES = (
    "_test.py",
    "_test.go",
    "_spec.rb",
    "_test.rb",
    "Test.java",
    "Tests.java",
    "Test.kt",
    "Tests.cs",
    "Test.cs",
)
_JS_EXTS = ("ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts")

# Functions of one family may call each other; a Python call never lands on a
# TypeScript function of the same name.
_FAMILY = {
    ".py": "py",
    ".pyw": "py",
    ".pyi": "py",
    **{f".{e}": "js" for e in _JS_EXTS},
    ".vue": "js",
    ".svelte": "js",
}


@lru_cache(maxsize=65536)
def is_test_path(path: str, root: str | None) -> bool:
    """True for a test file or a file under a test directory (relative to
    ``root``, so a repo checked out under a directory named tests/ is not
    all test code)."""
    rel = path
    if root:
        prefix = str(root).rstrip("/") + "/"
        if path.startswith(prefix):
            rel = path[len(prefix) :]
    parts = rel.replace("\\", "/").split("/")
    if any(p in _TEST_DIRS for p in parts[:-1]):
        return True
    base = parts[-1]
    if base == "conftest.py" or (base.startswith("test_") and base.endswith(".py")):
        return True
    if base.endswith(_TEST_SUFFIXES):
        return True
    stem, _, ext = base.rpartition(".")
    return ext in _JS_EXTS and (stem.endswith(".test") or stem.endswith(".spec"))


@lru_cache(maxsize=65536)
def family(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _FAMILY.get(ext, ext)


@lru_cache(maxsize=65536)
def real(path: str) -> str:
    """Canonical path, so a hint from the import resolver (which resolves
    symlinks) compares equal to a Function's file_path."""
    return os.path.realpath(path)


# ---------------------------------------------------------------------------
# Site rows, from a parsed file
# ---------------------------------------------------------------------------


class _Modules:
    """Resolve the modules one file imports to repo files, memoised.

    lookup returns the repo path, EXTERNAL for a module that is not in the
    repo, or "" when unsure (an unresolved relative import, or a name that
    exists in the repo but did not resolve).
    """

    def __init__(self, idx: FileIndex, root: Path | None) -> None:
        self.idx = idx
        self.root = root
        self.cache: dict[str, str] = {}

    def lookup(self, module: str) -> str:
        if module in self.cache:
            return self.cache[module]
        out = self._lookup(module)
        self.cache[module] = out
        return out

    def _lookup(self, module: str) -> str:
        if not module or self.root is None:
            return ""
        from codegraph.imports.resolver import resolve_import

        try:
            target = resolve_import(
                self.idx.lang, module, Path(self.idx.path), self.root
            )
        except Exception:
            return ""
        if target is not None:
            return real(str(target))
        return EXTERNAL if self._external(module) else ""

    def _external(self, module: str) -> bool:
        if self.idx.lang == "python":
            if module.startswith("."):
                return False
            from codegraph.imports.resolver import _python_source_roots

            top = module.split(".", 1)[0]
            importer_dir = Path(self.idx.path).resolve().parent
            for base in _python_source_roots(importer_dir, self.root.resolve()):
                if (base / top).exists() or (base / f"{top}.py").exists():
                    return False
            return True
        # JS/TS: a bare package specifier. Relative paths and the common
        # project aliases stay unsure when they do not resolve.
        return not module.startswith((".", "/", "~", "@/", "#"))


def _pyjoin(module: str, name: str) -> str:
    return f"{module}{name}" if module.endswith(".") else f"{module}.{name}"


def _shape(
    ref: CallRef,
    fn: SymbolDef,
    bases: dict[str, list[str]],
    modules: _Modules,
    lang: str,
) -> tuple[str, str, str]:
    """(kind, hint, ctx) of one call, see the kind constants."""
    if not ref.receiver:
        if ref.symbol == "*":
            # Only star imports can bind it: their repo files, if any.
            paths = {modules.lookup(m) for m in ref.module.split("|")}
            if "" in paths:
                return BARE, "", ""
            return BARE, "|".join(sorted(paths - {EXTERNAL})) or EXTERNAL, ""
        if ref.module and ref.symbol:
            return BARE, modules.lookup(ref.module), ""
        if lang == "python" and not ref.module:
            # Not defined here and not imported: a parameter, a local, a
            # nested function or a builtin, never a function of another file.
            return BARE, EXTERNAL, ""
        return BARE, "", ""
    if ref.receiver in (SELF, "super"):
        own = fn.class_name or ""
        own_bases = ",".join(bases.get(own, ()))
        return SELF, "", f"{'' if ref.receiver == 'super' else own}:{own_bases}"
    if ref.receiver == "?":
        return ATTR, "", ""
    if ref.receiver.endswith("()"):
        # C(...).f(): a method of C when C is a class. An import of C from
        # another library leaves nothing to link.
        name = ref.receiver[:-2]
        if not ref.module:
            return CLS, "", name
        path = modules.lookup(ref.module)
        if path == EXTERNAL:
            return MOD, EXTERNAL, ""
        cls = ref.symbol if ref.symbol and ref.symbol != "default" else name
        return CLS, path, cls
    if not ref.module:
        return ATTR, "", ""
    rest = ref.receiver.split(".")[1:]
    if lang == "python":
        head = _pyjoin(ref.module, ref.symbol) if ref.symbol else ref.module
        for k in range(len(rest), -1, -1):
            path = modules.lookup(".".join([head, *rest[:k]]))
            if path and path != EXTERNAL:
                return (MOD, path, "") if k == len(rest) else (CLS, path, rest[k])
        if ref.symbol:
            path = modules.lookup(ref.module)
            if path == EXTERNAL:
                return MOD, EXTERNAL, ""
            if path and not rest:
                return CLS, path, ref.symbol
            return ATTR, "", ""
        if modules.lookup(head) == EXTERNAL:
            return MOD, EXTERNAL, ""
        return ATTR, "", ""
    path = modules.lookup(ref.module)
    if path == EXTERNAL:
        return MOD, EXTERNAL, ""
    if not path:
        return ATTR, "", ""
    if not ref.symbol:
        return (MOD, path, "") if not rest else (CLS, path, rest[0])
    if rest:
        return ATTR, "", ""
    cls = ref.receiver if ref.symbol == "default" else ref.symbol
    return CLS, path, cls


def site_rows(
    idx: FileIndex, root: Path | None
) -> tuple[list[tuple[str, str, str, str, str, str]], frozenset[str]]:
    """The by-name call sites of a parsed file, as call_site rows
    (from_id, name, "", kind, hint, ctx), and the repo files the file
    imports, at module level or inside a function body.

    Names matching a language built-in callable are filtered out (see
    parsers/builtins.py) unless an import binds them, so callees like
    isinstance / println / parseInt don't accumulate spurious edges.
    """
    from codegraph.parsers.builtins import is_builtin

    lang = idx.lang
    modules = _Modules(idx, root)
    bases = {c.name: [b.rsplit(".", 1)[-1] for b in c.bases if b] for c in idx.classes}
    imported: set[str] = set()
    for imp in idx.imports:
        imported.add(modules.lookup(imp.source_module))
        if lang == "python":
            imported.update(
                modules.lookup(_pyjoin(imp.source_module, sym)) for sym in imp.symbols
            )
    rows: list[tuple[str, str, str, str, str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for fn in idx.functions:
        if fn.call_refs:
            for ref in fn.call_refs:
                unbound = not ref.module or ref.symbol == "*"
                if unbound and lang and is_builtin(lang, ref.name):
                    continue
                if ref.module and ref.symbol != "*":
                    imported.add(modules.lookup(ref.module))
                kind, hint, ctx = _shape(ref, fn, bases, modules, lang)
                if kind == ATTR and fn.class_name:
                    # The caller's class and bases, see unknown_receiver.
                    ctx = f"{fn.class_name}:{','.join(bases.get(fn.class_name, ()))}"
                row = (fn.id, ref.name, "", kind, hint, ctx)
                if row not in seen:
                    seen.add(row)
                    rows.append(row)
            continue
        for name in fn.calls:
            row = (fn.id, name, "", "", "", "")
            if row in seen or (lang and is_builtin(lang, name)):
                continue
            seen.add(row)
            rows.append(row)
    imported.discard("")
    imported.discard(EXTERNAL)
    imported.discard(real(idx.path))
    return rows, frozenset(imported)


# ---------------------------------------------------------------------------
# Targets of one site
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Site:
    from_id: str
    file_path: str
    name: str
    kind: str = ""
    hint: str = ""
    ctx: str = ""


@dataclass(frozen=True, slots=True)
class Target:
    """A function a site may call. ``bases`` are the base class names of
    the method's class, as its own file declares them."""

    id: str
    name: str
    file_path: str
    class_name: str = ""
    bases: tuple[str, ...] = ()


class _Pool:
    """The functions of one name a given caller may reach (its language
    family, test files only for test callers), indexed for the rules."""

    def __init__(self, targets: list[Target]) -> None:
        self.fns = [t for t in targets if not t.class_name]
        self.methods = [t for t in targets if t.class_name]
        self.by_file: dict[tuple[bool, str], list[Target]] = {}
        self.by_real: dict[tuple[bool, str], list[Target]] = {}
        self.by_class: dict[str, list[Target]] = {}
        for t in targets:
            is_method = bool(t.class_name)
            self.by_file.setdefault((is_method, t.file_path), []).append(t)
            self.by_real.setdefault((is_method, real(t.file_path)), []).append(t)
            if is_method:
                self.by_class.setdefault(t.class_name, []).append(t)
        self._roots: list[Target] | None = None

    def in_file(self, methods: bool, path: str) -> list[Target]:
        return self.by_file.get((methods, path), [])

    def from_hint(self, hint: str) -> list[Target] | None:
        """Functions in the hinted files, else in their packages (a hint on
        an __init__.py or index file), else None."""
        hints = hint.split("|")
        exact = [t for h in hints for t in self.by_real.get((False, h), ())]
        if exact:
            return exact
        dirs = tuple(
            os.path.dirname(h) + os.sep
            for h in hints
            if os.path.basename(h) == "__init__.py"
            or os.path.basename(h).startswith("index.")
        )
        if not dirs:
            return None
        return [t for t in self.fns if real(t.file_path).startswith(dirs)] or None

    def roots(self) -> list[Target]:
        """The methods no other candidate's class overrides from: when
        Child(Base) and Base both define f, Base.f."""
        if self._roots is None:
            classes = set(self.by_class)
            self._roots = [t for t in self.methods if not classes.intersection(t.bases)]
        return self._roots

    def few_roots(self, name: str) -> list[Target]:
        """The methods left once overriding ones are set aside, when they
        are few and the name is not one every library uses."""
        if name in COMMON_METHODS:
            return []
        roots = self.roots()
        return roots if len(roots) <= MAX_METHOD_FANOUT else []

    def unknown_receiver(self, site: Site, imported: frozenset[str]) -> list[Target]:
        """Methods an ``obj.f()`` on an object of unknown type may call: the
        ones of the caller's file or of a file it imports, else few_roots.
        The object is not the caller's own instance (that is a self call),
        so the caller's own class is left out: ``self.manager.update()``
        in ``Handler.update`` calls the manager, not itself."""
        caller = site.file_path
        qual = site.from_id.rsplit("::", 1)[-1]
        skip = {qual.rsplit(".", 1)[0]} if "." in qual else set()
        if site.kind == ATTR and site.name in COMMON_METHODS:
            # self.session.flush() in a BaseManager subclass: the file
            # imports BaseManager to inherit from it, not to call its flush.
            skip.update(b for b in site.ctx.partition(":")[2].split(",") if b)
        near = [t for t in self.in_file(True, caller) if t.class_name not in skip]
        for path in imported:
            near.extend(
                t
                for t in self.by_real.get((True, path), ())
                if t.class_name not in skip
            )
        if near:
            return list(dict.fromkeys(near))
        return self.few_roots(site.name)


class Candidates:
    """Every function of one name, with the pools of each kind of caller."""

    def __init__(self, targets: list[Target], root: str | None) -> None:
        self.root = root
        self.info = [
            (t, family(t.file_path), is_test_path(t.file_path, root)) for t in targets
        ]
        self._pools: dict[tuple[str, bool], _Pool] = {}

    def pool(self, fam: str, caller_test: bool) -> _Pool:
        key = (fam, caller_test)
        if key not in self._pools:
            self._pools[key] = _Pool(
                [
                    t
                    for t, f, test in self.info
                    if f == fam and (caller_test or not test)
                ]
            )
        return self._pools[key]


def targets_for(
    site: Site,
    candidates: Candidates | list[Target],
    root: str | None,
    imported: frozenset[str] = frozenset(),
) -> list[Target]:
    """The functions ``site`` calls among ``candidates`` (every function
    named ``site.name``). ``imported`` holds the repo files the caller's file
    imports. Production code never links into test files, and a call never
    crosses language families."""
    if not isinstance(candidates, Candidates):
        candidates = Candidates(candidates, root)
    caller = site.file_path
    pool = candidates.pool(family(caller), is_test_path(caller, root))
    kind = site.kind
    if not kind:
        local = pool.in_file(False, caller) + pool.in_file(True, caller)
        return local or pool.fns + pool.methods
    if kind == BARE:
        local = pool.in_file(False, caller)
        if local:
            return local
        if site.hint == EXTERNAL:
            return []
        return (pool.from_hint(site.hint) if site.hint else None) or pool.fns
    if kind == MOD:
        if site.hint == EXTERNAL:
            return []
        return (pool.from_hint(site.hint) if site.hint else None) or pool.fns
    if kind == SELF:
        own, _, bases = site.ctx.partition(":")
        if own:
            mine = [t for t in pool.in_file(True, caller) if t.class_name == own]
            if mine:
                return mine
        inherited = [t for b in bases.split(",") if b for t in pool.by_class.get(b, ())]
        if inherited:
            return inherited
        # Defined further up the hierarchy, or not in the repo at all (a
        # base from a library): the receiver's class is known, so the files
        # the caller imports say nothing about it.
        return pool.few_roots(site.name)
    if kind == CLS:
        found = pool.by_class.get(site.ctx)
        return found or pool.unknown_receiver(site, imported)
    return pool.unknown_receiver(site, imported)


def needs_imports(kind: str) -> bool:
    """Whether targets_for reads the caller's imported files for ``kind``."""
    return kind in (CLS, ATTR)
