# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Core utilities: single source of truth for shared helpers.

from codegraph.core.db import get_connection, get_db_path, get_readonly_connection, reset_connection
from .utils import lang_color, normalize_identifier, rows, safe_id, short_path

# init_schema lives in codegraph.core.schema (Kuzu) or codegraph.core.schema_duckdb
# (DuckDB). Import the right one directly — re-exporting from here would force
# `import codegraph.core` to load the Kuzu schema and trip an ImportError for
# users who installed cgh without the `kuzu` extra.

__all__ = [
    "get_connection",
    "get_readonly_connection",
    "get_db_path",
    "reset_connection",
    "rows",
    "short_path",
    "safe_id",
    "lang_color",
    "normalize_identifier",
]
