# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Opt-in cold-start phase timing. Off by default and free when
#              off; set CGH_TIMING=1 to have the proxy and the owner emit one
#              line per phase boundary to stderr (process start, owner
#              discovery, owner ready, bridge ready, engine sub-phases). Each
#              line carries a wall clock (time.time) so a caller can correlate
#              the gap between its own spawn of `cgh serve` and the proxy's
#              first phase, and a monotonic clock for same-process deltas. A
#              driver parses these to attribute cold-start milliseconds between
#              the Python proxy bridge, the owner engine, and the client
#              round-trip. The format is stable and machine-readable:
#
#                  CGHTIMING\t<label>\t<pid>\t<wall>\t<monotonic>
#
#              Kept as a permanent, cost-free instrument so the proxy/engine
#              latency split can be re-measured on demand without patching.

from __future__ import annotations

import os
import sys
import time

# Resolve the flag once at import. The emit path then costs a single attribute
# read and an early return when timing is off, so leaving the call sites in
# place carries no measurable overhead for normal runs.
_ENABLED = bool(os.environ.get("CGH_TIMING"))


def timing_enabled() -> bool:
    """Whether CGH_TIMING phase emission is on for this process."""
    return _ENABLED


def emit(label: str) -> None:
    """Emit one phase-boundary line to stderr when CGH_TIMING is set; a no-op
    otherwise. Never raises: a timing probe must not be able to break a serve."""
    if not _ENABLED:
        return
    try:
        sys.stderr.write(
            f"CGHTIMING\t{label}\t{os.getpid()}\t{time.time()!r}\t{time.monotonic()!r}\n"
        )
        sys.stderr.flush()
    except Exception:
        # A broken stderr (closed, full) must never take the serve down.
        pass
