# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Single source of truth for the cgh graph model: what
# nodes exist, what edges exist, and how each backend names them.
# Both KuzuGraphDB and DuckDBGraphDB consume this when implementing
# the upsert/edge/purge helpers from core.protocol.GraphDB.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeSpec:
    """Metadata about a node label / SQL table.

    ``label``     : Cypher label (matches core/schema.py).
    ``table``     : DuckDB table name.
    ``key_field`` : the unique key column / property (the PRIMARY KEY).
    ``has_file_path`` : True if the node carries a ``file_path`` column
        that we purge by when re-indexing a file.
    """

    label: str
    table: str
    key_field: str
    has_file_path: bool


# Node labels used by the indexer. Order matters for purge: edges first,
# then nodes — within nodes, no inter-table FK, so any order works.
NODES: dict[str, NodeSpec] = {
    "File": NodeSpec("File", "file", "path", False),
    "Function": NodeSpec("Function", "function", "id", True),
    "Class": NodeSpec("Class", "class", "id", True),
    "Endpoint": NodeSpec("Endpoint", "endpoint", "id", True),
    "TFResource": NodeSpec("TFResource", "tf_resource", "id", True),
    "TFVar": NodeSpec("TFVar", "tf_var", "id", True),
    "MdSection": NodeSpec("MdSection", "md_section", "id", True),
}


@dataclass(frozen=True)
class EdgeSpec:
    """Metadata about a relationship / SQL edge table.

    ``edge_type``    : Cypher relationship name (uppercase).
    ``table``        : DuckDB edge table name.
    ``src_label``    : Cypher label of the source node (e.g. "File").
    ``dst_label``    : Cypher label of the destination node.
    ``src_column``   : DuckDB column for the source key (e.g. "from_path").
    ``dst_column``   : DuckDB column for the destination key (e.g. "to_id").
    ``prop_columns`` : ordered prop column names if the edge carries
        property data (IMPORTS has "symbol", MD_LINKS_TO has "label", ...).
    """

    edge_type: str
    table: str
    src_label: str
    dst_label: str
    src_column: str
    dst_column: str
    prop_columns: tuple[str, ...] = ()


EDGES: dict[str, EdgeSpec] = {
    "IMPORTS": EdgeSpec(
        "IMPORTS", "edge_imports", "File", "File", "from_path", "to_path", ("symbol",)
    ),
    "DEFINES_FN": EdgeSpec(
        "DEFINES_FN", "edge_defines_fn", "File", "Function", "from_path", "to_id"
    ),
    "DEFINES_CLASS": EdgeSpec(
        "DEFINES_CLASS", "edge_defines_class", "File", "Class", "from_path", "to_id"
    ),
    "CALLS": EdgeSpec("CALLS", "edge_calls", "Function", "Function", "from_id", "to_id"),
    "INHERITS": EdgeSpec("INHERITS", "edge_inherits", "Class", "Class", "from_id", "to_id"),
    "HAS_METHOD": EdgeSpec(
        "HAS_METHOD", "edge_has_method", "Class", "Function", "from_id", "to_id"
    ),
    "TF_DEPENDS": EdgeSpec(
        "TF_DEPENDS", "edge_tf_depends", "TFResource", "TFResource", "from_id", "to_id"
    ),
    "DEFINES_RESOURCE": EdgeSpec(
        "DEFINES_RESOURCE",
        "edge_defines_resource",
        "File",
        "TFResource",
        "from_path",
        "to_id",
    ),
    "DEFINES_TFVAR": EdgeSpec(
        "DEFINES_TFVAR", "edge_defines_tfvar", "File", "TFVar", "from_path", "to_id"
    ),
    "DEFINES_SECTION": EdgeSpec(
        "DEFINES_SECTION",
        "edge_defines_section",
        "File",
        "MdSection",
        "from_path",
        "to_id",
    ),
    "MD_LINKS_TO": EdgeSpec(
        "MD_LINKS_TO",
        "edge_md_links_to",
        "MdSection",
        "File",
        "from_id",
        "to_path",
        ("label",),
    ),
    "MD_REFS_SYMBOL": EdgeSpec(
        "MD_REFS_SYMBOL",
        "edge_md_refs_symbol",
        "MdSection",
        "Function",
        "from_id",
        "to_id",
        ("context",),
    ),
    "MD_REFS_CLASS": EdgeSpec(
        "MD_REFS_CLASS",
        "edge_md_refs_class",
        "MdSection",
        "Class",
        "from_id",
        "to_id",
        ("context",),
    ),
    "CONTAINS_SECTION": EdgeSpec(
        "CONTAINS_SECTION",
        "edge_contains_section",
        "MdSection",
        "MdSection",
        "from_id",
        "to_id",
    ),
    "DEFINES_ENDPOINT": EdgeSpec(
        "DEFINES_ENDPOINT",
        "edge_defines_endpoint",
        "File",
        "Endpoint",
        "from_path",
        "to_id",
    ),
    "IMPLEMENTED_BY": EdgeSpec(
        "IMPLEMENTED_BY",
        "edge_implemented_by",
        "Endpoint",
        "Function",
        "from_id",
        "to_id",
    ),
}


def edges_touching(label: str) -> list[EdgeSpec]:
    """Return every edge whose source or destination is ``label``.

    Used by purge_file_data to know which edge tables to clean before
    deleting a node row. Order is irrelevant for correctness.
    """
    return [e for e in EDGES.values() if e.src_label == label or e.dst_label == label]
