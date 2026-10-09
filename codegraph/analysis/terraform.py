# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-09
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The Terraform side of the graph. At index time: store a .tf
#              file's blocks, turn the addresses their expressions use into
#              persisted name references scoped to the module directory
#              (Terraform resolves var / local / resource addresses within
#              one directory), and resolve those references from either
#              end so the edges never depend on file order. Module sources
#              resolve locally or through the opt-in [terraform]
#              module_sources mapping; a mapped directory outside the index
#              has its variables and outputs read in on demand. At query
#              time: the reference edges seen as callers / callees, and the
#              block-precise blast radius of a change, for find_callers,
#              find_callees, impact_of and cgh impact.

from __future__ import annotations

import os
from typing import Any

from codegraph.core.graph_model import TF_REF_EDGES

# Name references (see indexer.NameRef) the Terraform blocks record:
#   tf_ref     block id -> "<module dir>::<address>" it uses. A module
#              block's inputs reference "<source dir>::var.<input>".
#   tf_modout  block id -> "<module dir>::module.<m>", extra = the output
#              name: `module.m.out` resolves to output "out" of the
#              directory module m's source points to.
#   tf_modres  block id -> "<module dir>::module.<m>", extra = a resource
#              address inside module m: a moved / import / removed block
#              naming module.m.google_x.y resolves to that resource of the
#              directory module m's source points to.
TF_REF = "tf_ref"
TF_MODOUT = "tf_modout"
TF_MODRES = "tf_modres"

_VAR_PREFIXES = ("var.", "output.", "tfvars.")
_TF_FIELDS = ["id", "address", "kind"]
_RES_FIELDS = ["id", "address", "kind", "source_dir"]


def label_of(node_id: str) -> str:
    """TFVar for a variable, output or tfvars entry id, else TFResource."""
    address = node_id.rpartition("::")[2]
    return "TFVar" if address.startswith(_VAR_PREFIXES) else "TFResource"


def label_for(res) -> str:
    return "TFVar" if res.kind in ("variable", "output", "tfvars") else "TFResource"


def _split_source(source: str) -> tuple[str, str]:
    """(package, subdir) of a module source: the `?ref=` query dropped, the
    `//subdir` split off (a scheme's `://` is not a subdir marker)."""
    source = source.partition("?")[0]
    scheme = source.find("://")
    cut = source.find("//", scheme + 3 if scheme >= 0 else 0)
    if cut < 0:
        return source, ""
    return source[:cut], source[cut + 2 :].strip("/")


def _norm_package(package: str) -> str:
    """A remote package for prefix matching: no `git::` forcing prefix, no
    trailing slash, no `.git` suffix."""
    package = package.strip()
    if package.startswith("git::"):
        package = package[len("git::") :]
    package = package.rstrip("/")
    if package.endswith(".git"):
        package = package[: -len(".git")]
    return package


class ModuleSources:
    """Where module sources point on disk, for one indexed repo.

    A local source (./ or ../) is a directory next to the caller. A remote
    one (git, registry, http) points nowhere unless the opt-in
    ``[terraform] module_sources`` table maps its package to a local
    checkout: the longest matching prefix wins, the `//subdir` is joined to
    the mapped directory and the `?ref=` is ignored (the checkout may sit at
    another ref than the one pinned). Nothing is ever fetched.
    """

    def __init__(
        self,
        root: str = "",
        mapping: dict[str, str] | None = None,
        indexed: list[str] | None = None,
        excluded: list[str] | None = None,
    ) -> None:
        self.root = root
        pairs = []
        for prefix, target in (mapping or {}).items():
            key = _norm_package(str(prefix))
            if key and target:
                path = os.path.normpath(os.path.join(root or os.sep, str(target)))
                pairs.append((key, path))
        self.mapping = sorted(pairs, key=lambda p: -len(p[0]))
        self.indexed = [os.path.normpath(d) for d in (indexed or [])]
        self.excluded = [os.path.normpath(d) for d in (excluded or [])]

    @classmethod
    def from_config(cls, cfg, root) -> ModuleSources:
        """Built from a loaded CodegraphConfig: the mapping, the repo root
        and its extra_dirs (indexed here), the federated subrepos (not)."""
        root_s = os.path.abspath(str(root)) if root else ""
        if cfg is None or not root_s:
            return cls(root_s)

        def _abs(p: str) -> str:
            return os.path.normpath(os.path.join(root_s, str(p)))

        extra = [_abs(d) for d in getattr(cfg, "extra_dirs", []) or []]
        subs = [_abs(d) for d in getattr(cfg, "subrepos", []) or []]
        return cls(
            root_s,
            dict(getattr(cfg, "terraform_module_sources", {}) or {}),
            [root_s, *extra],
            subs,
        )

    def resolve(self, module_dir: str, source: str) -> str:
        """The directory ``source`` points to, or "" when unknown."""
        if source.startswith(("./", "../")):
            return os.path.normpath(os.path.join(module_dir, source))
        if not self.mapping or not source:
            return ""
        package, subdir = _split_source(source)
        package = _norm_package(package)
        for prefix, target in self.mapping:
            if package == prefix:
                rest = ""
            elif package.startswith(prefix + "/"):
                rest = package[len(prefix) + 1 :]
            else:
                continue
            return os.path.normpath(os.path.join(target, rest, subdir))
        return ""

    def is_indexed(self, directory: str) -> bool:
        """True when ``directory`` is indexed into this graph (under the
        repo root or an extra_dir, outside any federated subrepo)."""

        def under(base: str) -> bool:
            return directory == base or directory.startswith(
                base.rstrip(os.sep) + os.sep
            )

        if any(under(d) for d in self.excluded):
            return False
        return any(under(d) for d in self.indexed)


_LOCAL_ONLY = ModuleSources()


def source_dir(
    module_dir: str, source: str, sources: ModuleSources | None = None
) -> str:
    """The directory a module source points to.

    Terraform reads a source starting with ./ or ../ as a local directory.
    Anything else (registry, git, http, s3) is remote and gets a directory
    only through the ``[terraform] module_sources`` mapping.
    """
    return (sources or _LOCAL_ONLY).resolve(module_dir, source)


def _key(directory: str, address: str) -> str:
    return f"{directory}::{address}"


# ---------------------------------------------------------------------------
# Index time
# ---------------------------------------------------------------------------


def ingest_blocks(
    conn, resources: list, sources: ModuleSources | None = None, defines: bool = True
) -> None:
    """Upsert every block of one file and its DEFINES edge (``defines`` off
    for the blocks of a module read outside the index, which has no File
    node)."""
    for res in resources:
        module_dir = os.path.dirname(res.file_path)
        if label_for(res) == "TFVar":
            conn.upsert_node(
                "TFVar",
                "id",
                res.id,
                {
                    "name": res.name,
                    "kind": res.kind,
                    "file_path": res.file_path,
                    "start_line": res.start_line,
                    "end_line": res.end_line,
                    "address": res.address,
                    "module_dir": module_dir,
                },
            )
            if defines:
                conn.ensure_edge("DEFINES_TFVAR", res.file_path, res.id)
        else:
            conn.upsert_node(
                "TFResource",
                "id",
                res.id,
                {
                    "name": res.name,
                    "type": res.type,
                    "file_path": res.file_path,
                    "start_line": res.start_line,
                    "end_line": res.end_line,
                    "kind": res.kind,
                    "address": res.address,
                    "module_dir": module_dir,
                    "source_dir": (
                        source_dir(module_dir, res.source, sources)
                        if res.kind == "module"
                        else ""
                    ),
                },
            )
            if defines:
                conn.ensure_edge("DEFINES_RESOURCE", res.file_path, res.id)


# Blocks whose refs are whole state addresses (module.m.google_x.y), not
# expression references.
_ADDRESS_KINDS = ("moved", "import", "removed")


def _state_rows(res, module_dir: str) -> set[tuple[str, str, str, str]]:
    """References of a moved / import / removed block: the module call
    (module.m) and, through it, the resource inside the module's source
    (tf_modres), or the resource of this directory."""
    rows: set[tuple[str, str, str, str]] = set()
    for ref in res.refs:
        parts = ref.split(".")
        if parts[0] == "module" and len(parts) >= 2:
            module = _key(module_dir, f"module.{parts[1]}")
            rows.add((TF_REF, res.id, module, ""))
            rest = parts[2:]
            width = 3 if rest[:1] == ["data"] else 2
            if len(rest) >= width and rest[0] != "module":
                rows.add((TF_MODRES, res.id, module, ".".join(rest[:width])))
        elif parts[0] == "data" and len(parts) >= 3:
            rows.add((TF_REF, res.id, _key(module_dir, ".".join(parts[:3])), ""))
        elif len(parts) >= 2:
            rows.add((TF_REF, res.id, _key(module_dir, ".".join(parts[:2])), ""))
    return rows


def ref_rows(
    resources: list, sources: ModuleSources | None = None
) -> list[tuple[str, str, str, str]]:
    """The name references one file's blocks record."""
    rows: set[tuple[str, str, str, str]] = set()
    for res in resources:
        module_dir = os.path.dirname(res.file_path)
        if res.kind in _ADDRESS_KINDS:
            rows |= _state_rows(res, module_dir)
            continue
        for ref in res.refs:
            parts = ref.split(".")
            if parts[0] == "module" and len(parts) >= 3:
                module = f"module.{parts[1]}"
                rows.add((TF_REF, res.id, _key(module_dir, module), ""))
                rows.add((TF_MODOUT, res.id, _key(module_dir, module), parts[2]))
            else:
                rows.add((TF_REF, res.id, _key(module_dir, ref), ""))
        if res.kind in ("module", "module_arg"):
            target = source_dir(module_dir, res.source, sources)
            if target:
                for name in res.inputs:
                    rows.add((TF_REF, res.id, _key(target, f"var.{name}"), ""))
    return sorted(rows)


def external_module_dirs(resources: list, sources: ModuleSources | None) -> list[str]:
    """Module directories this file's calls point to that no index covers
    (outside the repo and its extra_dirs, or in a federated subrepo)."""
    if sources is None or not sources.mapping:
        return []
    out: set[str] = set()
    for res in resources:
        if res.kind != "module":
            continue
        target = sources.resolve(os.path.dirname(res.file_path), res.source)
        if target and not sources.is_indexed(target):
            out.add(target)
    return sorted(out)


# (id(conn), directory) -> the file signature last read into that graph.
_EXTERNAL_SEEN: dict[tuple[int, str], tuple] = {}


def _dir_signature(directory: str) -> tuple:
    try:
        entries = [
            e for e in os.scandir(directory) if e.is_file() and e.name.endswith(".tf")
        ]
        out = []
        for e in sorted(entries, key=lambda e: e.name):
            st = e.stat()
            out.append((e.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(out)


def ingest_external_module(conn, directory: str) -> None:
    """Read the variables and outputs of a module directory outside the
    index into this graph, so the calls pointing at it link their inputs
    and outputs. Read-only on that directory: nothing is written there and
    nothing is fetched. Its resources stay out (their own module's graph
    is canonical for them), and so do its files: no File node, no FTS.
    Skipped when the directory is unchanged since it was last read into
    this graph."""
    from codegraph.parsers.terraform import TerraformParser

    sig = _dir_signature(directory)
    seen_key = (id(conn), directory)
    if (
        sig
        and _EXTERNAL_SEEN.get(seen_key) == sig
        and conn.find_nodes("TFVar", where={"module_dir": directory}, limit=1)
    ):
        return
    parser = TerraformParser()
    current = {os.path.join(directory, name) for name, _m, _s in sig}
    # A file removed from that directory since the last read: drop its copy.
    for row in conn.find_nodes(
        "TFVar", where={"module_dir": directory}, return_fields=["file_path"]
    ):
        gone = row["file_path"]
        if gone not in current and not conn.find_nodes(
            "File", where={"path": gone}, limit=1
        ):
            conn.purge_file_data(gone)
    for name, _mtime, _size in sig:
        path = os.path.join(directory, name)
        if conn.find_nodes("File", where={"path": path}, limit=1):
            continue  # indexed for real after all: never overwrite it
        blocks = [
            r for r in parser.parse(path).resources if r.kind in ("variable", "output")
        ]
        conn.purge_file_data(path)
        ingest_blocks(conn, blocks, defines=False)
        resolve_inbound(conn, path, blocks)
    _EXTERNAL_SEEN[seen_key] = sig


class _Blocks:
    """Lazy per-directory view of the Terraform blocks in the graph."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self._dirs: dict[str, dict[str, list[tuple[str, str, str]]]] = {}

    def in_dir(self, directory: str) -> dict[str, list[tuple[str, str, str]]]:
        """{address: [(id, label, source_dir)]} for one module directory."""
        if directory not in self._dirs:
            found: dict[str, list[tuple[str, str, str]]] = {}
            for label, fields in (("TFResource", _RES_FIELDS), ("TFVar", _TF_FIELDS)):
                for row in self._conn.find_nodes(
                    label, where={"module_dir": directory}, return_fields=fields
                ):
                    found.setdefault(row["address"] or "", []).append(
                        (str(row["id"]), label, row.get("source_dir") or "")
                    )
            self._dirs[directory] = found
        return self._dirs[directory]

    def at(self, key: str) -> list[tuple[str, str, str]]:
        directory, _, address = key.rpartition("::")
        return self.in_dir(directory).get(address, [])


def _edges_for(refs, blocks: _Blocks) -> dict[str, list[tuple[str, str]]]:
    edges: dict[str, list[tuple[str, str]]] = {}
    for kind, from_id, name, extra in refs:
        targets: list[tuple[str, str]] = []
        if kind == TF_REF:
            targets = [(tid, label) for tid, label, _s in blocks.at(name)]
        elif kind in (TF_MODOUT, TF_MODRES):
            inner = f"output.{extra}" if kind == TF_MODOUT else extra
            for _mid, _label, src in blocks.at(name):
                if src:
                    targets.extend(
                        (tid, label) for tid, label, _s in blocks.at(_key(src, inner))
                    )
        src_label = label_of(from_id)
        for tid, label in targets:
            if tid != from_id:
                edges.setdefault(TF_REF_EDGES[(src_label, label)], []).append(
                    (from_id, tid)
                )
    return edges


def _write(conn, edges: dict[str, list[tuple[str, str]]]) -> None:
    for edge_type, rows in edges.items():
        if rows:
            conn.ensure_edges(edge_type, rows)


def resolve_outbound(conn, refs: list[tuple[str, str, str, str]]) -> None:
    """Link one file's references to the blocks already in the graph."""
    if refs:
        _write(conn, _edges_for(refs, _Blocks(conn)))


def resolve_inbound(conn, file_path: str, resources: list) -> None:
    """Link the references OTHER files hold to the blocks ``file_path`` defines.

    A reference whose target was not indexed yet found nothing, and a
    reindex of the target's file drops every edge into it, so this pass is
    what makes the Terraform edges independent of indexing order.
    """
    if not resources:
        return
    module_dir = os.path.dirname(file_path)
    names = {_key(module_dir, res.address) for res in resources if res.address}
    outputs = {
        res.address[len("output.") :] for res in resources if res.kind == "output"
    }
    inner = {
        res.address
        for res in resources
        if res.kind in ("resource", "data") and res.address
    }
    rows = conn.name_refs_into(sorted(names), file_path) if names else []
    if outputs or inner:
        # module.m.out (or a moved block's module.m.google_x.y) written
        # against a module whose source is this directory: those references
        # are keyed by the module block.
        callers = [
            _key(r["module_dir"], r["address"])
            for r in conn.find_nodes(
                "TFResource",
                where={"source_dir": module_dir},
                return_fields=["module_dir", "address"],
            )
        ]
        if callers:
            rows += [
                r
                for r in conn.name_refs_into(sorted(set(callers)), file_path)
                if (r[0] == TF_MODOUT and r[4] in outputs)
                or (r[0] == TF_MODRES and r[4] in inner)
            ]
    refs = [
        (kind, from_id, name, extra)
        for kind, from_id, _f, name, extra in rows
        if kind in (TF_REF, TF_MODOUT, TF_MODRES)
    ]
    if refs:
        _write(conn, _edges_for(refs, _Blocks(conn)))


# ---------------------------------------------------------------------------
# Query time
# ---------------------------------------------------------------------------


def _edge_types_into(label: str) -> list[str]:
    return [e for (_s, d), e in TF_REF_EDGES.items() if d == label]


def _edge_types_from(label: str) -> list[str]:
    return [e for (s, _d), e in TF_REF_EDGES.items() if s == label]


# Kinds the query tools report a TFResource row under, by its block kind. A
# TFVar row (variable, output, tfvars entry) stays "tf_var", type = its kind.
TF_RESOURCE_KINDS = (
    "tf_resource",
    "tf_data",
    "tf_module",
    "tf_module_arg",
    "tf_local",
    "tf_provider",
    "tf_moved",
    "tf_import",
    "tf_removed",
)


def tool_kind(label: str, kind: str | None) -> str:
    if label == "TFVar":
        return "tf_var"
    return "tf_resource" if kind in (None, "", "resource") else f"tf_{kind}"


_LOOKUP_FIELDS = {
    "TFResource": [
        "id",
        "name",
        "type",
        "kind",
        "address",
        "file_path",
        "start_line",
        "end_line",
    ],
    "TFVar": ["id", "name", "kind", "address", "file_path", "start_line", "end_line"],
}


def _hit(label: str, row: dict[str, Any]) -> dict[str, Any]:
    end = row.get("end_line") or row.get("start_line")
    return {
        "kind": tool_kind(label, row.get("kind")),
        "name": row.get("address") or row.get("name"),
        "type": row.get("type") if label == "TFResource" else row.get("kind"),
        "file": row.get("file_path"),
        "start_line": row.get("start_line"),
        "end_line": end,
    }


def lookup(conn, name: str) -> list[dict[str, Any]]:
    """Terraform blocks matching ``name``: by address (var.region,
    google_x.y, module.m) or, as before addresses existed, by bare block
    name (region). Rows: {kind, name (the address), type, file,
    start_line, end_line}."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, fields in _LOOKUP_FIELDS.items():
        for field in ("address", "name"):
            for row in conn.find_nodes(
                label, where={field: name}, return_fields=fields
            ):
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                out.append(_hit(label, row))
    return out


def search(
    conn, query: str, limit: int, name_only: bool, wanted
) -> list[dict[str, Any]]:
    """Substring search over Terraform blocks for search_symbols. ``wanted``
    is the tool kinds to keep (empty keeps every kind)."""
    out: list[dict[str, Any]] = []
    for label, fields in _LOOKUP_FIELDS.items():
        kinds = ("tf_var",) if label == "TFVar" else TF_RESOURCE_KINDS
        if wanted and not any(k in wanted for k in kinds):
            continue
        match = {"name": query, "address": query}
        if not name_only:
            match["kind" if label == "TFVar" else "type"] = query
        for row in conn.find_nodes(
            label, contains=match, return_fields=fields, limit=limit
        ):
            hit = _hit(label, row)
            if not wanted or hit["kind"] in wanted:
                out.append(hit)
    return out


def nodes_named(conn, name: str) -> list[dict[str, Any]]:
    """TF blocks whose address is ``name``, any directory, both labels."""
    out: list[dict[str, Any]] = []
    for label in ("TFResource", "TFVar"):
        for row in conn.find_nodes(
            label,
            where={"address": name},
            return_fields=["id", "file_path", "start_line"],
        ):
            out.append({**row, "label": label})
    return out


def users_of(conn, name: str) -> list[dict[str, Any]]:
    """Blocks whose expressions reference the block(s) at address ``name``:
    rows of {src_address, src_file_path, src_start_line, dst_file_path}."""
    rows: list[dict[str, Any]] = []
    for label in ("TFResource", "TFVar"):
        for edge in _edge_types_into(label):
            rows.extend(
                conn.find_neighbors(
                    edge,
                    dst_where={"address": name},
                    return_src=["address", "file_path", "start_line"],
                    return_dst=["file_path"],
                )
            )
    return rows


def used_by(conn, name: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Blocks the block(s) at address ``name`` reference: rows of
    {dst_address, dst_file_path, dst_start_line}."""
    rows: list[dict[str, Any]] = []
    for label in ("TFResource", "TFVar"):
        for edge in _edge_types_from(label):
            rows.extend(
                conn.find_neighbors(
                    edge,
                    src_where={"address": name},
                    return_dst=["address", "file_path", "start_line"],
                    limit=limit,
                )
            )
    # Two blocks of that address (prod and staging) often reference the
    # same target: list it once.
    unique: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        unique.setdefault(
            (r["dst_address"], r["dst_file_path"], r["dst_start_line"]), r
        )
    return list(unique.values())


def referrers(conn, node_id: str, limit: int | None = None) -> list[str]:
    """Ids of the blocks referencing the block ``node_id``."""
    out: list[str] = []
    for edge in _edge_types_into(label_of(node_id)):
        out.extend(
            str(r["src_id"])
            for r in conn.find_neighbors(
                edge, dst_key=node_id, return_src=["id"], limit=limit
            )
        )
    return out


def blocks_in_file(conn, file_path: str) -> list[dict[str, Any]]:
    """The Terraform blocks a file defines, ordered by line:
    [{id, address, kind, start_line, end_line}]."""
    out: list[dict[str, Any]] = []
    for label in ("TFResource", "TFVar"):
        out.extend(
            conn.find_nodes(
                label,
                where={"file_path": file_path},
                return_fields=["id", "address", "kind", "start_line", "end_line"],
            )
        )
    out.sort(key=lambda r: r.get("start_line") or 0)
    return out


def dependent_files(conn, file_path: str, limit: int | None = None) -> list[str]:
    """Other files holding a block that references a block of ``file_path``.

    The Terraform counterpart of "files importing this file": a .tf file
    has no import statement, its blocks reference each other by address.
    Non-Terraform paths return [] without touching the graph.
    """
    if not file_path.endswith((".tf", ".tfvars")):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for block in blocks_in_file(conn, file_path):
        for src in referrers(conn, str(block["id"]), limit=limit):
            path = src.rpartition("::")[0]
            if path and path != file_path and path not in seen:
                seen.add(path)
                out.append(path)
    return out


def blocks_touching(
    conn, file_path: str, ranges: list[tuple[int, int]] | None = None
) -> list[dict[str, Any]]:
    """The blocks of ``file_path`` overlapping any (first, last) line range,
    or every block when ``ranges`` is None. A module input lies inside its
    module block, so a changed input yields both."""
    blocks = blocks_in_file(conn, file_path)
    if ranges is None:
        return blocks
    out = []
    for b in blocks:
        start = b.get("start_line") or 0
        end = b.get("end_line") or start
        if any(start <= hi and end >= lo for lo, hi in ranges):
            out.append(b)
    return out


def impacted_blocks(
    conn,
    start_ids: list[str],
    max_depth: int = 3,
    cap: int = 300,
    fanout: int = 500,
) -> tuple[list[str], bool]:
    """Reverse BFS over the Terraform reference edges from ``start_ids``:
    the blocks that reference them, then the blocks referencing those, up
    to ``max_depth`` hops. Module boundaries are crossed both ways: a
    module output is referenced by the blocks using module.m.out, and a
    module variable by the module call (and its input) feeding it.
    Returns (ordered block ids, truncated), start ids excluded."""
    seen = set(start_ids)
    frontier = list(start_ids)
    ordered: list[str] = []
    truncated = False
    depth = 0
    while frontier and depth < max(1, int(max_depth)):
        depth += 1
        nxt: list[str] = []
        for node_id in frontier:
            srcs = referrers(conn, node_id, limit=fanout)
            if len(srcs) >= fanout:
                truncated = True
            for src in srcs:
                if src in seen:
                    continue
                seen.add(src)
                ordered.append(src)
                nxt.append(src)
                if len(ordered) >= cap:
                    return ordered, True
        frontier = nxt
    return ordered, truncated


def impacted_files(
    conn,
    changes: dict[str, list[tuple[int, int]] | None],
    max_depth: int = 3,
    cap: int = 300,
) -> tuple[list[str], list[str], bool]:
    """Terraform blast radius of a change set, block-precise.

    ``changes`` maps a .tf / .tfvars path to its changed line ranges (None:
    the whole file). The changed blocks are the ones overlapping those
    lines; the result is (files holding a block that transitively
    references a changed block, the changed block ids, truncated). The
    changed files themselves are left out of the file list.
    """
    start: list[str] = []
    for path, ranges in changes.items():
        start += [str(b["id"]) for b in blocks_touching(conn, path, ranges)]
    ids, truncated = impacted_blocks(conn, start, max_depth=max_depth, cap=cap)
    files: list[str] = []
    seen = set(changes)
    for node_id in ids:
        path = node_id.rpartition("::")[0]
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files, start, truncated


def mention_terms(conn, block_ids: list[str]) -> list[str]:
    """What other files would write to mean the changed blocks: their
    address (google_x.y, var.v), `module.m.` for a module call or one of
    its inputs (contracts name module outputs that way), and `output.o`
    as the bare output name is too common to search."""
    terms: set[str] = set()
    for node_id in block_ids:
        address = node_id.rpartition("::")[2]
        parts = address.split(".")
        if parts[0] in ("tfvars", "moved", "import", "removed", "provider"):
            continue
        if parts[0] == "module" and len(parts) >= 2:
            terms.add(f"module.{parts[1]}.")
        elif parts[0] in ("var", "local", "output", "data") or len(parts) == 2:
            terms.add(address)
    return sorted(t for t in terms if len(t) >= 5)
