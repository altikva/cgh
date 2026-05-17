# Backward-compatibility shim — canonical source is codegraph.core.schema
from codegraph.core.schema import (  # noqa: F401
    EDGE_TABLES,
    NODE_TABLES,
    init_schema,
)
