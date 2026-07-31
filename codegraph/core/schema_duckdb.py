# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: DuckDB schema for the code graph. Mirrors core/schema.py
# table-for-table so MCP tools can query the same graph in either backend.
#
# Naming convention vs Kuzu:
#   - Node tables keep their Kuzu names lowercased: file, function, class, ...
#   - Edge tables become `edge_<rel>` with from_<pk> / to_<pk> columns so a
#     JOIN reads almost like Cypher's MATCH a)-[:CALLS]->(b).
#
# Edge tables don't declare FOREIGN KEY constraints. DuckDB rejects
# `ON DELETE CASCADE`, and emulating it via a plain FK would force the
# indexer to delete edges before nodes anyway. Since the indexer's
# _purge_file() already issues DELETE statements for every node type
# touching a file, we match that pattern: edges are plain TEXT columns
# that the indexer cleans up explicitly. Same end state as Kuzu's
# DETACH DELETE without the constraint dance.

from __future__ import annotations

import duckdb

# ---------------------------------------------------------------------------
# Node tables
# ---------------------------------------------------------------------------
NODE_TABLES = [
    """CREATE TABLE IF NOT EXISTS file (
        path          TEXT PRIMARY KEY,
        lang          TEXT,
        mtime         DOUBLE,
        git_blob_sha  TEXT,
        role          TEXT,
        layer         TEXT,
        module_doc    TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS endpoint (
        id          TEXT PRIMARY KEY,
        method      TEXT,
        path        TEXT,
        framework   TEXT,
        file_path   TEXT,
        start_line  BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS function (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        file_path   TEXT,
        start_line  BIGINT,
        end_line    BIGINT,
        docstring   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS class (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        file_path   TEXT,
        start_line  BIGINT,
        end_line    BIGINT,
        docstring   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tf_resource (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        type        TEXT,
        file_path   TEXT,
        start_line  BIGINT,
        end_line    BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS tf_var (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        kind        TEXT,
        file_path   TEXT,
        start_line  BIGINT
    )""",
    """CREATE TABLE IF NOT EXISTS md_section (
        id              TEXT PRIMARY KEY,
        title           TEXT,
        level           BIGINT,
        file_path       TEXT,
        start_line      BIGINT,
        end_line        BIGINT,
        body_preview    TEXT,
        anchor          TEXT
    )""",
]


# ---------------------------------------------------------------------------
# Edge tables
# ---------------------------------------------------------------------------
# Each row is one (from, to) tuple plus any edge properties. Composite
# primary key prevents duplicate edges, mirroring Kuzu's MERGE semantics
# when the indexer issues "MERGE (a)-[:X]->(b)".
EDGE_TABLES = [
    """CREATE TABLE IF NOT EXISTS edge_imports (
        from_path  TEXT,
        to_path    TEXT,
        symbol     TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_path, to_path, symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_fn (
        from_path  TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_class (
        from_path  TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_calls (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_inherits (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_has_method (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_tf_depends (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_resource (
        from_path  TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_tfvar (
        from_path  TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_section (
        from_path  TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_md_links_to (
        from_id    TEXT,
        to_path    TEXT,
        label      TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_id, to_path, label)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_md_refs_symbol (
        from_id    TEXT,
        to_id      TEXT,
        context    TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_id, to_id, context)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_md_refs_class (
        from_id    TEXT,
        to_id      TEXT,
        context    TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (from_id, to_id, context)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_contains_section (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_defines_endpoint (
        from_path  TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_path, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_implemented_by (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
]


# ---------------------------------------------------------------------------
# Reverse-lookup indexes
# ---------------------------------------------------------------------------
# Edges already get an index for free via their composite PK, but queries
# like "who imports X" scan the reverse direction (to_path). DuckDB doesn't
# auto-index the second column of a composite PK, so we add explicit ones.
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_imports_to ON edge_imports(to_path)",
    "CREATE INDEX IF NOT EXISTS idx_calls_to ON edge_calls(to_id)",
    "CREATE INDEX IF NOT EXISTS idx_inherits_to ON edge_inherits(to_id)",
    "CREATE INDEX IF NOT EXISTS idx_function_file ON function(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_class_file ON class(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_md_section_file ON md_section(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_endpoint_file ON endpoint(file_path)",
]


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the DuckDB tables and indexes. Idempotent via IF NOT EXISTS."""
    for ddl in NODE_TABLES + EDGE_TABLES + INDEXES:
        conn.execute(ddl)
