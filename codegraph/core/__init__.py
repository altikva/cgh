# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Core utilities: single source of truth for shared helpers.

from codegraph.core.db import (
    get_connection,
    get_db_path,
    get_readonly_connection,
    reset_connection,
)

from .utils import lang_color, normalize_identifier, rows, safe_id, short_path

# init_schema lives in codegraph.core.schema_duckdb; the SQLite backend
# builds its own schema in codegraph.core.db_sqlite. Import the right one
# directly rather than re-exporting from here.

__all__ = [
    "get_connection",
    "get_db_path",
    "get_readonly_connection",
    "lang_color",
    "normalize_identifier",
    "reset_connection",
    "rows",
    "safe_id",
    "short_path",
]
