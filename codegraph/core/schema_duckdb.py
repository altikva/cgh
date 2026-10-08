# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: DuckDB schema for the code graph. Defines the node and
# edge tables so MCP tools can query the graph.
#
# Naming convention:
#   - Node tables are lowercased label names: file, function, class, ...
#   - Edge tables become `edge_<rel>` with from_<pk> / to_<pk> columns so a
#     JOIN reads almost like a graph MATCH a)-[:CALLS]->(b).
#
# Edge tables don't declare FOREIGN KEY constraints. DuckDB rejects
# `ON DELETE CASCADE`, and emulating it via a plain FK would force the
# indexer to delete edges before nodes anyway. Since the indexer's
# _purge_file() already issues DELETE statements for every node type
# touching a file, we match that pattern: edges are plain TEXT columns
# that the indexer cleans up explicitly.

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
    # Terraform blocks. `address` is the in-module address (google_x.y,
    # data.t.n, module.m, local.l, var.v, output.o) and `module_dir` the
    # directory it resolves in; a module block keeps its raw source in
    # `type` and the local directory it points to in `source_dir`.
    """CREATE TABLE IF NOT EXISTS tf_resource (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        type        TEXT,
        file_path   TEXT,
        start_line  BIGINT,
        end_line    BIGINT,
        kind        TEXT,
        address     TEXT,
        module_dir  TEXT,
        source_dir  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tf_var (
        id          TEXT PRIMARY KEY,
        name        TEXT,
        kind        TEXT,
        file_path   TEXT,
        start_line  BIGINT,
        end_line    BIGINT,
        address     TEXT,
        module_dir  TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS md_section (
        id              TEXT PRIMARY KEY,
        title           TEXT,
        level           BIGINT,
        file_path       TEXT,
        start_line      BIGINT,
        end_line        BIGINT,
        body_preview    TEXT,
        anchor          TEXT,
        kind            TEXT
    )""",
]


# ---------------------------------------------------------------------------
# Edge tables
# ---------------------------------------------------------------------------
# Each row is one (from, to) tuple plus any edge properties. Composite
# primary key prevents duplicate edges, so re-ingesting the same edge is
# idempotent.
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
    """CREATE TABLE IF NOT EXISTS edge_tf_refs_var (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_tf_var_depends (
        from_id    TEXT,
        to_id      TEXT,
        PRIMARY KEY (from_id, to_id)
    )""",
    """CREATE TABLE IF NOT EXISTS edge_tf_var_refs (
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
# Call sites
# ---------------------------------------------------------------------------
# Every call a function makes, kept by callee NAME (to_id empty) or, for the
# precise resolver, by resolved target id. CALLS edges are derived from these
# rows, so reindexing a callee's file can relink the callers in other files
# instead of losing them. No PK: the indexer dedupes before writing.
SIDE_TABLES = [
    """CREATE TABLE IF NOT EXISTS call_site (
        from_id    TEXT,
        file_path  TEXT,
        name       TEXT,
        to_id      TEXT NOT NULL DEFAULT ''
    )""",
    # Every other reference resolved by name, kept so its edge can be rebuilt
    # from either end: a class base (kind "inherits"), a markdown code mention
    # ("md_ref", extra = context), a markdown link ("md_link", name = target
    # path, extra = label) and an endpoint handler defined in another file
    # ("handler", extra = the candidate file). No PK: rows are deduped first.
    """CREATE TABLE IF NOT EXISTS name_ref (
        kind       TEXT,
        from_id    TEXT,
        file_path  TEXT,
        name       TEXT,
        extra      TEXT NOT NULL DEFAULT ''
    )""",
    # The File mtime each file had when this format last indexed it. A
    # writer that predates the table (cgh 0.15 after a rollback) updates the
    # File node but not this row, and leaves the file's call sites and name
    # references as they were: a missing or different stamp is how the next
    # index finds the files to parse again. No PK: rows are replaced by a
    # delete then insert, like the tables above.
    """CREATE TABLE IF NOT EXISTS file_stamp (
        path       TEXT,
        mtime      DOUBLE
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
    "CREATE INDEX IF NOT EXISTS idx_call_site_name ON call_site(name)",
    "CREATE INDEX IF NOT EXISTS idx_call_site_file ON call_site(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_call_site_to ON call_site(to_id)",
    "CREATE INDEX IF NOT EXISTS idx_name_ref_name ON name_ref(name)",
    "CREATE INDEX IF NOT EXISTS idx_name_ref_file ON name_ref(file_path)",
    "CREATE INDEX IF NOT EXISTS idx_file_stamp_path ON file_stamp(path)",
    "CREATE INDEX IF NOT EXISTS idx_tf_resource_dir ON tf_resource(module_dir)",
    "CREATE INDEX IF NOT EXISTS idx_tf_var_dir ON tf_var(module_dir)",
]


# Columns added after a graph may already exist on disk. ADD COLUMN IF NOT
# EXISTS keeps an older index readable instead of failing on the first write.
MIGRATIONS = [
    "ALTER TABLE md_section ADD COLUMN IF NOT EXISTS kind TEXT",
    "ALTER TABLE tf_resource ADD COLUMN IF NOT EXISTS kind TEXT",
    "ALTER TABLE tf_resource ADD COLUMN IF NOT EXISTS address TEXT",
    "ALTER TABLE tf_resource ADD COLUMN IF NOT EXISTS module_dir TEXT",
    "ALTER TABLE tf_resource ADD COLUMN IF NOT EXISTS source_dir TEXT",
    "ALTER TABLE tf_var ADD COLUMN IF NOT EXISTS end_line BIGINT",
    "ALTER TABLE tf_var ADD COLUMN IF NOT EXISTS address TEXT",
    "ALTER TABLE tf_var ADD COLUMN IF NOT EXISTS module_dir TEXT",
]


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Create the DuckDB tables and indexes. Idempotent via IF NOT EXISTS."""
    # Migrations run before the indexes: an index can name a column an
    # older table only gets from its migration.
    for ddl in NODE_TABLES + EDGE_TABLES + SIDE_TABLES:
        conn.execute(ddl)
    for migration in MIGRATIONS:
        conn.execute(migration)
    for ddl in INDEXES:
        conn.execute(ddl)
