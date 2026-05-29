# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Core utilities — single source of truth for shared helpers.

from .db import get_connection, get_db_path, get_readonly_connection, reset_connection
from .schema import init_schema
from .utils import lang_color, normalize_identifier, rows, safe_id, short_path

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
    "init_schema",
]
