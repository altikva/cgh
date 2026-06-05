# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: terminate() must stop a process cross-platform and never raise on
#              a pid that is already gone.

from __future__ import annotations

import subprocess
import sys

from codegraph.state.pidfile import process_alive, terminate


def test_terminate_stops_a_running_process():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_alive(p.pid)
        terminate(p.pid, graceful_timeout=2.0)
        # The child takes SIGTERM (default disposition) and exits; reap it.
        p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=5)
    assert not process_alive(p.pid)


def test_terminate_is_noop_for_dead_or_invalid_pid():
    # Must not raise.
    terminate(2_000_000_000, graceful_timeout=0.1)
    terminate(0)
    terminate(-1)
