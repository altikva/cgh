# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The interface methods an implementation method answers for,
#              computed at query time. A call on an attribute typed with an
#              interface (`self._kms: KmsClient`) links to the interface's
#              method, never to the classes implementing it, so the callers
#              of an implementation are the callers of its own method plus
#              those of every interface method it implements. A class
#              implements P when it inherits P (directly or through its
#              bases: ABCs, TS `implements`) or, for a `typing.Protocol`,
#              when it defines every method P declares (structural typing;
#              dunders ignored). Nothing is stored: the CALLS edges stay
#              precise and only the query widens.

from __future__ import annotations

from typing import Any

from codegraph.analysis.call_rules import MAX_CLASS_DEPTH

# Base names that make a class a structural interface. A dotted base
# (typing.Protocol, typing_extensions.Protocol) keeps its last segment.
_PROTOCOL_BASES = frozenset({"Protocol"})
# Direct bases that make a class an interface whose methods a traversal
# may follow to their single implementation: a Protocol or an ABC.
_INTERFACE_BASES = frozenset({"Protocol", "ABC"})


def _base(name: str) -> str:
    """A base name as recorded (``typing.Protocol``, ``Protocol[T]``)
    reduced to its last plain segment."""
    return name.split("[", 1)[0].rsplit(".", 1)[-1]


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def label(fn_id: str) -> str:
    """The `Class.method` (or bare name) part of a Function id."""
    return fn_id.rsplit("::", 1)[-1]


def _module_matches(file_path: str, module: list[str]) -> bool:
    """True when ``file_path`` is the module written ``module`` (dotted
    parts, a trailing part of its path: `services.envelope_encryption` for
    app/services/envelope_encryption.py, or a package's __init__)."""
    if not module:
        return True
    stem = file_path.replace("\\", "/").rsplit(".", 1)[0]
    if stem.endswith("/__init__") or stem.endswith("/index"):
        stem = stem.rsplit("/", 1)[0]
    want = "/".join(module)
    return stem == want or stem.endswith("/" + want)


def qualified_methods(conn: Any, name: str) -> list[str]:
    """Ids of the Functions a dotted ``name`` selects: `Class.method`
    (id ending with `::Class.method`), `module.Class.method` (that, in a
    file the module path names) or `module.function`. Empty for a bare
    name."""
    parts = name.split(".")
    if len(parts) < 2 or not all(parts):
        return []
    method = parts[-1]
    out: set[str] = set()
    for r in conn.find_nodes(
        "Function", where={"name": method}, return_fields=["id", "file_path"]
    ):
        fn_id = str(r.get("id") or "")
        path = str(r.get("file_path") or "")
        qual = label(fn_id)
        if (qual == ".".join(parts[-2:]) and _module_matches(path, parts[:-2])) or (
            qual == method and _module_matches(path, parts[:-1])
        ):
            out.add(fn_id)
    return sorted(out)


class InterfaceResolver:
    """Per-query cache of class methods, bases and protocol candidates."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self._owner: dict[str, str] = {}
        self._methods: dict[str, dict[str, str]] = {}
        self._ancestors: dict[str, list[str]] = {}
        self._protocols: dict[str, list[tuple[str, str]]] = {}
        self._by_name: dict[str, list[tuple[str, str, tuple[str, ...]]]] = {}
        self._impls: dict[str, list[str]] = {}

    def owner(self, fn_id: str) -> str:
        """The class id defining method ``fn_id``, "" for a plain function."""
        if fn_id not in self._owner:
            rows = self.conn.find_neighbors(
                "HAS_METHOD", dst_key=fn_id, return_src=["id"], limit=1
            )
            self._owner[fn_id] = str(rows[0]["src_id"]) if rows else ""
        return self._owner[fn_id]

    def methods(self, class_id: str) -> dict[str, str]:
        """{method name: Function id} of the methods ``class_id`` defines."""
        if class_id not in self._methods:
            self._methods[class_id] = {
                str(r["dst_name"]): str(r["dst_id"])
                for r in self.conn.find_neighbors(
                    "HAS_METHOD", src_key=class_id, return_dst=["id", "name"]
                )
            }
        return self._methods[class_id]

    def ancestors(self, class_id: str) -> list[str]:
        """Class ids ``class_id`` inherits from, nearest first, up to
        MAX_CLASS_DEPTH levels."""
        if class_id not in self._ancestors:
            out: list[str] = []
            seen = {class_id}
            level = [class_id]
            for _ in range(MAX_CLASS_DEPTH):
                nxt: list[str] = []
                for cid in level:
                    for r in self.conn.find_neighbors(
                        "INHERITS", src_key=cid, return_dst=["id"]
                    ):
                        pid = str(r["dst_id"])
                        if pid not in seen:
                            seen.add(pid)
                            nxt.append(pid)
                out += sorted(nxt)
                level = nxt
                if not level:
                    break
            self._ancestors[class_id] = out
        return self._ancestors[class_id]

    def _named(self, method: str) -> list[tuple[str, str, tuple[str, ...]]]:
        """(Function id, class id, class base names) of every function named
        ``method``; the class id is "" for a plain function."""
        if method not in self._by_name:
            self._by_name[method] = sorted(
                (fn_id, class_id, tuple(bases))
                for fn_id, _n, _f, class_id, bases in self.conn.call_targets_named(
                    [method]
                )
            )
        return self._by_name[method]

    def _protocol_methods(self, method: str) -> list[tuple[str, str]]:
        """(Function id, class id) of every method named ``method`` declared
        by a class with a Protocol base."""
        if method not in self._protocols:
            self._protocols[method] = [
                (fn_id, class_id)
                for fn_id, class_id, bases in self._named(method)
                if class_id and any(_base(b) in _PROTOCOL_BASES for b in bases)
            ]
        return self._protocols[method]

    def is_interface(self, fn_id: str) -> bool:
        """True for a method of a class declaring an interface: a
        ``typing.Protocol`` or an ``abc.ABC`` (a direct base)."""
        method = label(fn_id).rsplit(".", 1)[-1]
        if _is_dunder(method):
            return False
        return any(
            fid == fn_id and cid and any(_base(b) in _INTERFACE_BASES for b in bases)
            for fid, cid, bases in self._named(method)
        )

    def implementations(self, fn_id: str) -> list[str]:
        """Ids of the methods implementing the interface method ``fn_id``
        (the same-named methods whose interfaces_of include it). Empty when
        ``fn_id`` is no interface method."""
        if fn_id not in self._impls:
            found: list[str] = []
            if self.is_interface(fn_id):
                method = label(fn_id).rsplit(".", 1)[-1]
                found = [
                    fid
                    for fid, cid, _b in self._named(method)
                    if cid and fid != fn_id and fn_id in self.interfaces_of(fid)
                ]
            self._impls[fn_id] = found
        return self._impls[fn_id]

    def sole_implementation(self, fn_id: str) -> str:
        """The one implementation of the interface method ``fn_id``, "" when
        it has none or several (a call through it cannot be followed
        without guessing)."""
        impls = self.implementations(fn_id)
        return impls[0] if len(impls) == 1 else ""

    def interfaces_of(self, fn_id: str) -> list[str]:
        """Function ids of the interface methods ``fn_id`` implements: the
        same-named method of every class its class inherits, and of every
        Protocol its class (with its bases) conforms to. Empty for a plain
        function or a dunder."""
        method = label(fn_id).rsplit(".", 1)[-1]
        cls = self.owner(fn_id)
        if not cls or _is_dunder(method):
            return []
        out: list[str] = []
        lineage = self.ancestors(cls)
        for anc in lineage:
            target = self.methods(anc).get(method)
            if target and target != fn_id and target not in out:
                out.append(target)
        candidates = [
            (pid, pcls)
            for pid, pcls in self._protocol_methods(method)
            if pcls != cls and pcls not in lineage and pid not in out
        ]
        if candidates:
            defined = {n for n in self.methods(cls) if not _is_dunder(n)}
            for anc in lineage:
                defined.update(n for n in self.methods(anc) if not _is_dunder(n))
            for pid, pcls in candidates:
                wanted = {n for n in self.methods(pcls) if not _is_dunder(n)}
                if wanted and wanted <= defined:
                    out.append(pid)
        return out


def interface_methods_in(conn: Any, file_path: str) -> list[str]:
    """Ids of the interface methods implemented by the methods ``file_path``
    defines, outside the file's own functions."""
    own = sorted(
        str(r["id"])
        for r in conn.find_nodes(
            "Function", where={"file_path": file_path}, return_fields=["id"]
        )
        if r.get("id")
    )
    resolver = InterfaceResolver(conn)
    found = {i for fid in own for i in resolver.interfaces_of(fid)}
    return sorted(found - set(own))


def interface_caller_files(conn: Any, file_path: str, limit: int = 500) -> list[str]:
    """Files holding a function that calls, through an interface, a method
    ``file_path`` implements: the callers of the interface methods it
    implements. ``file_path`` itself is left out."""
    out: set[str] = set()
    for iface in interface_methods_in(conn, file_path):
        for r in conn.find_neighbors(
            "CALLS", dst_key=iface, return_src=["file_path"], limit=limit
        ):
            path = r.get("src_file_path") or ""
            if path and path != file_path:
                out.add(path)
    return sorted(out)
