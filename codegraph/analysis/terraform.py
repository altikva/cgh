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
#              end so the edges never depend on file order. At query time:
#              the reference edges seen as callers / callees, and the files
#              that depend on a .tf file, for find_callers, find_callees,
#              impact_of and cgh impact.

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
TF_REF = "tf_ref"
TF_MODOUT = "tf_modout"

_VAR_PREFIXES = ("var.", "output.", "tfvars.")
_TF_FIELDS = ["id", "address", "kind"]
_RES_FIELDS = ["id", "address", "kind", "source_dir"]


def label_of(node_id: str) -> str:
    """TFVar for a variable, output or tfvars entry id, else TFResource."""
    address = node_id.rpartition("::")[2]
    return "TFVar" if address.startswith(_VAR_PREFIXES) else "TFResource"


def label_for(res) -> str:
    return "TFVar" if res.kind in ("variable", "output", "tfvars") else "TFResource"


def source_dir(module_dir: str, source: str) -> str:
    """The directory a module source points to, for a local path only.

    Terraform reads a source starting with ./ or ../ as a local directory;
    anything else (registry, git, http, s3) is remote and gets no link.
    """
    if not source.startswith(("./", "../")):
        return ""
    return os.path.normpath(os.path.join(module_dir, source))


def _key(directory: str, address: str) -> str:
    return f"{directory}::{address}"


# ---------------------------------------------------------------------------
# Index time
# ---------------------------------------------------------------------------


def ingest_blocks(conn, resources: list) -> None:
    """Upsert every block of one file and its DEFINES edge."""
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
                    "source_dir": source_dir(module_dir, res.source),
                },
            )
            conn.ensure_edge("DEFINES_RESOURCE", res.file_path, res.id)


def ref_rows(resources: list) -> list[tuple[str, str, str, str]]:
    """The name references one file's blocks record."""
    rows: set[tuple[str, str, str, str]] = set()
    for res in resources:
        module_dir = os.path.dirname(res.file_path)
        for ref in res.refs:
            parts = ref.split(".")
            if parts[0] == "module" and len(parts) >= 3:
                module = f"module.{parts[1]}"
                rows.add((TF_REF, res.id, _key(module_dir, module), ""))
                rows.add((TF_MODOUT, res.id, _key(module_dir, module), parts[2]))
            else:
                rows.add((TF_REF, res.id, _key(module_dir, ref), ""))
        target = source_dir(module_dir, res.source) if res.kind == "module" else ""
        if target:
            for name in res.inputs:
                rows.add((TF_REF, res.id, _key(target, f"var.{name}"), ""))
    return sorted(rows)


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
        elif kind == TF_MODOUT:
            for _mid, _label, src in blocks.at(name):
                if src:
                    targets.extend(
                        (tid, label)
                        for tid, label, _s in blocks.at(_key(src, f"output.{extra}"))
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
    rows = conn.name_refs_into(sorted(names), file_path) if names else []
    if outputs:
        # module.m.out written against a module whose source is this
        # directory: those references are keyed by the module block.
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
                if r[0] == TF_MODOUT and r[4] in outputs
            ]
    refs = [
        (kind, from_id, name, extra)
        for kind, from_id, _f, name, extra in rows
        if kind in (TF_REF, TF_MODOUT)
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
TF_RESOURCE_KINDS = ("tf_resource", "tf_data", "tf_module", "tf_local", "tf_provider")


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
