# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2025-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2025 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Kuzu graph schema — node and edge table DDL for the code index.

import kuzu

# ---------------------------------------------------------------------------
# Node tables
# ---------------------------------------------------------------------------
NODE_TABLES = [
    # Source file
    """CREATE NODE TABLE IF NOT EXISTS File(
        path    STRING,
        lang    STRING,
        mtime   DOUBLE,
        PRIMARY KEY (path)
    )""",
    # Function or method
    """CREATE NODE TABLE IF NOT EXISTS Function(
        id          STRING,
        name        STRING,
        file_path   STRING,
        start_line  INT64,
        end_line    INT64,
        docstring   STRING,
        PRIMARY KEY (id)
    )""",
    # Class
    """CREATE NODE TABLE IF NOT EXISTS Class(
        id          STRING,
        name        STRING,
        file_path   STRING,
        start_line  INT64,
        end_line    INT64,
        docstring   STRING,
        PRIMARY KEY (id)
    )""",
    # Terraform resource  (type = "aws_s3_bucket" etc.)
    """CREATE NODE TABLE IF NOT EXISTS TFResource(
        id          STRING,
        name        STRING,
        type        STRING,
        file_path   STRING,
        start_line  INT64,
        end_line    INT64,
        PRIMARY KEY (id)
    )""",
    # Terraform variable / output
    """CREATE NODE TABLE IF NOT EXISTS TFVar(
        id          STRING,
        name        STRING,
        kind        STRING,
        file_path   STRING,
        start_line  INT64,
        PRIMARY KEY (id)
    )""",
    # Markdown section (heading + body)
    """CREATE NODE TABLE IF NOT EXISTS MdSection(
        id              STRING,
        title           STRING,
        level           INT64,
        file_path       STRING,
        start_line      INT64,
        end_line        INT64,
        body_preview    STRING,
        anchor          STRING,
        PRIMARY KEY (id)
    )""",
]

# ---------------------------------------------------------------------------
# Edge tables
# ---------------------------------------------------------------------------
EDGE_TABLES = [
    # File imports another file / module
    "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO File, symbol STRING)",
    # File defines a function
    "CREATE REL TABLE IF NOT EXISTS DEFINES_FN(FROM File TO Function)",
    # File defines a class
    "CREATE REL TABLE IF NOT EXISTS DEFINES_CLASS(FROM File TO Class)",
    # Function calls another function (best-effort, unresolved calls use name only)
    "CREATE REL TABLE IF NOT EXISTS CALLS(FROM Function TO Function)",
    # Class inherits from another class
    "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Class TO Class)",
    # Class contains a method
    "CREATE REL TABLE IF NOT EXISTS HAS_METHOD(FROM Class TO Function)",
    # Terraform resource references another (depends_on / interpolation)
    "CREATE REL TABLE IF NOT EXISTS TF_DEPENDS(FROM TFResource TO TFResource)",
    # File contains a TF resource
    "CREATE REL TABLE IF NOT EXISTS DEFINES_RESOURCE(FROM File TO TFResource)",
    # File contains a TF var/output
    "CREATE REL TABLE IF NOT EXISTS DEFINES_TFVAR(FROM File TO TFVar)",
    # File contains a Markdown section
    "CREATE REL TABLE IF NOT EXISTS DEFINES_SECTION(FROM File TO MdSection)",
    # Markdown section links to a file (internal doc links)
    "CREATE REL TABLE IF NOT EXISTS MD_LINKS_TO(FROM MdSection TO File, label STRING)",
    # Markdown section references a code symbol (inline code / fenced block)
    "CREATE REL TABLE IF NOT EXISTS MD_REFS_SYMBOL(FROM MdSection TO Function, context STRING)",
    "CREATE REL TABLE IF NOT EXISTS MD_REFS_CLASS(FROM MdSection TO Class, context STRING)",
    # Section hierarchy: parent heading contains child heading
    "CREATE REL TABLE IF NOT EXISTS CONTAINS_SECTION(FROM MdSection TO MdSection)",
]


def init_schema(conn: kuzu.Connection) -> None:
    """Create all node and edge tables (idempotent via IF NOT EXISTS)."""
    for ddl in NODE_TABLES + EDGE_TABLES:
        conn.execute(ddl)
