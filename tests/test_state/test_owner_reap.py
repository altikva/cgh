# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: _reap_child() must call waitpid to clear zombie processes without
#              raising on invalid or non-child PIDs.

from __future__ import annotations

import os
import time

import pytest

from codegraph.state.ipc import _reap_child

pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs POSIX fork")


def test_reaps_a_dead_child():
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    time.sleep(0.05)
    _reap_child(pid)
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, 0)


def test_none_and_zero_are_noops():
    _reap_child(None)
    _reap_child(0)


def test_non_child_pid_is_swallowed():
    _reap_child(999999)
