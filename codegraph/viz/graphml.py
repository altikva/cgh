# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: GraphML export of the code graph for `cgh export --format
#              graphml`. write_graphml streams every node label of the graph
#              model (files, functions, classes, endpoints, Terraform blocks,
#              Markdown sections) and every edge type, with their key
#              properties: an IMPORTS edge's symbol, a CALLS edge's
#              resolution (analysis/provenance.py), a doc edge's label or
#              context. Paths are written repo-relative, text is escaped with
#              the stdlib, and characters XML 1.0 forbids are dropped, so the
#              file opens in any GraphML reader. Backend-neutral: every read
#              goes through the GraphDB protocol.

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TextIO
from xml.sax.saxutils import escape, quoteattr

from codegraph.core.graph_model import EDGES, NODES

GRAPHML_NS = "http://graphml.graphdrawing.org/xmlns"

# (attribute name, GraphML type) declared once per domain. Every node or
# edge writes only the attributes it has.
NODE_KEYS: tuple[tuple[str, str], ...] = (
    ("label", "string"),
    ("kind", "string"),
    ("name", "string"),
    ("file", "string"),
    ("start_line", "int"),
    ("end_line", "int"),
    ("lang", "string"),
    ("role", "string"),
    ("layer", "string"),
    ("method", "string"),
    ("route", "string"),
    ("framework", "string"),
    ("router", "string"),
    ("tf_type", "string"),
    ("subkind", "string"),
    ("level", "int"),
    ("anchor", "string"),
)
EDGE_KEYS: tuple[tuple[str, str], ...] = (
    ("type", "string"),
    ("symbol", "string"),
    ("resolution", "string"),
    ("edge_label", "string"),
    ("context", "string"),
)

# Graph-model columns read per label, and the GraphML attribute each one
# becomes. The key column (path or id) is read separately.
_NODE_FIELDS: dict[str, dict[str, str]] = {
    "File": {"lang": "lang", "role": "role", "layer": "layer"},
    "Function": {
        "name": "name",
        "file_path": "file",
        "start_line": "start_line",
        "end_line": "end_line",
    },
    "Class": {
        "name": "name",
        "file_path": "file",
        "start_line": "start_line",
        "end_line": "end_line",
    },
    "Endpoint": {
        "method": "method",
        "path": "route",
        "framework": "framework",
        "router": "router",
        "file_path": "file",
        "start_line": "start_line",
    },
    "TFResource": {
        "name": "name",
        "type": "tf_type",
        "file_path": "file",
        "start_line": "start_line",
        "end_line": "end_line",
    },
    "TFVar": {
        "name": "name",
        "kind": "subkind",
        "file_path": "file",
        "start_line": "start_line",
        "end_line": "end_line",
    },
    "MdSection": {
        "title": "name",
        "level": "level",
        "kind": "subkind",
        "anchor": "anchor",
        "file_path": "file",
        "start_line": "start_line",
        "end_line": "end_line",
    },
}
# Edge property columns and the GraphML attribute each becomes.
_EDGE_PROPS = {"symbol": "symbol", "label": "edge_label", "context": "context"}

# Characters XML 1.0 does not allow, even escaped.
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


def _clean(value: Any) -> str:
    return _BAD_XML.sub("", str(value))


class _Paths:
    """Makes stored absolute paths, and the ids built on them, relative to
    the repo root. Paths outside it (extra directories) stay as stored."""

    def __init__(self, root: str | Path) -> None:
        self.prefix = str(Path(root).resolve()).rstrip(os.sep) + os.sep

    def rel(self, path: str) -> str:
        path = str(path or "")
        if path.startswith(self.prefix):
            return path[len(self.prefix) :].replace(os.sep, "/")
        return path

    def rel_id(self, key: str) -> str:
        """A Function / Class / block id starts with its file path."""
        key = str(key or "")
        file_part, sep, rest = key.partition("::")
        return f"{self.rel(file_part)}{sep}{rest}" if sep else self.rel(key)


def _in_scope(file_rel: str, scope: str) -> bool:
    if not scope:
        return True
    return file_rel == scope or file_rel.startswith(scope + "/")


def _node_id(label: str, rel_key: str) -> str:
    return f"{label}:{rel_key}"


def _display(label: str, rel_key: str, attrs: dict[str, Any]) -> str:
    if label == "File":
        return rel_key
    if label == "Endpoint":
        return f"{attrs.get('method') or ''} {attrs.get('route') or ''}".strip()
    if label in ("TFResource", "TFVar"):
        return rel_key.rsplit("::", 1)[-1]
    return str(attrs.get("name") or rel_key.rsplit("::", 1)[-1])


def _data(key: str, value: Any, indent: str) -> str:
    return f'{indent}<data key="{key}">{escape(_clean(value))}</data>\n'


def _key_id(domain: str, name: str) -> str:
    return f"{domain[0]}_{name}"


def write_graphml(
    conn: Any, root: str | Path, out: TextIO, scope: str = ""
) -> dict[str, Any]:
    """Write the graph of ``root`` to ``out`` as GraphML; return counts.

    ``scope`` (a repo-relative directory or file) keeps only the nodes
    whose file lies under it, and the edges between two kept nodes."""
    from codegraph.analysis.provenance import EdgeProvenance

    paths = _Paths(root)
    scope = scope.strip().strip("/").replace(os.sep, "/")
    if scope in (".", ""):
        scope = ""

    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(
        f'<graphml xmlns="{GRAPHML_NS}" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:schemaLocation="{GRAPHML_NS} {GRAPHML_NS}/1.0/graphml.xsd">\n'
    )
    for domain, keys in (("node", NODE_KEYS), ("edge", EDGE_KEYS)):
        for name, typ in keys:
            out.write(
                f'  <key id="{_key_id(domain, name)}" for="{domain}" '
                f'attr.name="{name}" attr.type="{typ}"/>\n'
            )
    out.write('  <graph id="cgh" edgedefault="directed">\n')

    node_counts: dict[str, int] = {}
    emitted: set[str] = set()
    for label, spec in NODES.items():
        fields = _NODE_FIELDS.get(label, {})
        rows = conn.find_nodes(
            label,
            return_fields=[spec.key_field, *fields],
            order_by=[spec.key_field],
        )
        count = 0
        for row in rows:
            key = row.get(spec.key_field)
            if not key:
                continue
            rel_key = paths.rel_id(str(key))
            attrs: dict[str, Any] = {}
            for col, attr in fields.items():
                value = row.get(col)
                if value is None or value == "":
                    continue
                attrs[attr] = paths.rel(value) if attr == "file" else value
            file_rel = rel_key if label == "File" else str(attrs.get("file") or "")
            if label == "File":
                attrs["file"] = rel_key
            if not _in_scope(file_rel, scope):
                continue
            node_id = _node_id(label, rel_key)
            if node_id in emitted:
                continue
            emitted.add(node_id)
            count += 1
            out.write(f"    <node id={quoteattr(_clean(node_id))}>\n")
            out.write(_data("n_label", _display(label, rel_key, attrs), "      "))
            out.write(_data("n_kind", label, "      "))
            for name, typ in NODE_KEYS:
                if name in ("label", "kind") or name not in attrs:
                    continue
                value = attrs[name]
                if typ == "int":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        continue
                out.write(_data(_key_id("node", name), value, "      "))
            out.write("    </node>\n")
        node_counts[label] = count

    edge_counts: dict[str, int] = {}
    provenance = EdgeProvenance(conn)
    n = 0
    for edge_type, spec in EDGES.items():
        src_key = NODES[spec.src_label].key_field
        dst_key = NODES[spec.dst_label].key_field
        return_dst = [dst_key] + (["name"] if edge_type == "CALLS" else [])
        rows = conn.find_neighbors(
            edge_type,
            return_src=[src_key],
            return_dst=return_dst,
            return_edge=list(spec.prop_columns),
        )
        resolutions: list[str] = []
        if edge_type == "CALLS" and rows:
            calls = [
                {
                    "src_id": r.get(f"src_{src_key}"),
                    "dst_id": r.get(f"dst_{dst_key}"),
                    "dst_name": r.get("dst_name"),
                }
                for r in rows
            ]
            resolutions = provenance.of_rows(calls)
        count = 0
        for i, row in enumerate(rows):
            src = _node_id(spec.src_label, paths.rel_id(row.get(f"src_{src_key}")))
            dst = _node_id(spec.dst_label, paths.rel_id(row.get(f"dst_{dst_key}")))
            if src not in emitted or dst not in emitted:
                continue
            out.write(
                f'    <edge id="e{n}" source={quoteattr(_clean(src))} '
                f"target={quoteattr(_clean(dst))}>\n"
            )
            n += 1
            count += 1
            out.write(_data("e_type", edge_type, "      "))
            for col in spec.prop_columns:
                value = row.get(f"edge_{col}")
                attr = _EDGE_PROPS.get(col)
                if attr and value not in (None, ""):
                    out.write(_data(_key_id("edge", attr), value, "      "))
            if resolutions:
                out.write(_data("e_resolution", resolutions[i], "      "))
            out.write("    </edge>\n")
        edge_counts[edge_type] = count

    out.write("  </graph>\n</graphml>\n")
    return {
        "nodes": sum(node_counts.values()),
        "edges": sum(edge_counts.values()),
        "nodes_by_label": node_counts,
        "edges_by_type": edge_counts,
        "scope": scope,
    }
