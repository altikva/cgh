# Backward-compatibility shim — canonical source is codegraph.core.db
from codegraph.core.db import (  # noqa: F401
    get_connection,
    get_db_path,
    get_readonly_connection,
    reset_connection,
)
