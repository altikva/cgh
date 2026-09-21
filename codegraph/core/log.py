# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Logging for background processes. Modules declare
#              `logging.getLogger(__name__)`; the entrypoints of the
#              owner, the proxy and the standalone watcher call
#              configure_background_logging() once. CLI user output
#              stays print/rich; nothing here touches the root logger,
#              so an embedding application keeps full control.

from __future__ import annotations

import logging
import sys

# The leading timestamp lets a reader place an owner.log line against a
# specific event (a crash window, a restart) instead of guessing from its
# position in the file. Without it, "when did this error happen" is
# unanswerable, which is exactly what a live incident ran into.
_FORMAT = "[codegraph] %(asctime)s %(levelname).1s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def configure_background_logging(level: int = logging.INFO) -> None:
    """Attach one stderr handler to the 'codegraph' logger, idempotent.
    stderr is the right sink: the owner's stderr is redirected to
    .codegraph/owner.log at spawn, and the MCP proxy must keep stdout
    clean for the protocol. Unconfigured (library/CLI) processes still
    surface WARNING+ through logging's last-resort handler."""
    logger = logging.getLogger("codegraph")
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
