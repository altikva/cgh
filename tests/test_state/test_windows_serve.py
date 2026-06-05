# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-05
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Windows-portability regressions for `cgh serve`: signals that do
#              not exist on Windows must be skipped, liveness must not use
#              os.kill (which terminates on Windows), and a CRLF-tainted
#              command token must still parse.

from __future__ import annotations

import os
import subprocess
import sys

from codegraph.state.pidfile import process_alive


class TestResolveSignals:
    def test_returns_existing_signals(self):
        from codegraph.server import _resolve_signals

        sigs = _resolve_signals("SIGTERM", "SIGINT")
        assert len(sigs) == 2

    def test_skips_missing_without_raising(self):
        from codegraph.server import _resolve_signals

        # A name that exists nowhere must be skipped, never raise (this is
        # exactly how SIGHUP behaves on Windows).
        sigs = _resolve_signals("SIGTERM", "SIG_DOES_NOT_EXIST")
        assert len(sigs) == 1

    def test_all_missing_returns_empty(self):
        from codegraph.server import _resolve_signals

        assert _resolve_signals("NOPE_A", "NOPE_B") == []


class TestProcessAlive:
    def test_current_process_is_alive(self):
        assert process_alive(os.getpid()) is True

    def test_out_of_range_pid_is_not_alive(self):
        # Above any platform's pid_max, so it cannot exist and cannot be
        # reused. Confirms the probe reports dead rather than erroring.
        assert process_alive(2_000_000_000) is False

    def test_nonpositive_pid_is_not_alive(self):
        assert process_alive(0) is False
        assert process_alive(-1) is False


class TestCrlfCommand:
    def test_trailing_cr_on_command_is_tolerated(self):
        # A token like "--version\r" (CRLF wrapper on Windows) must still work.
        out = subprocess.run(
            [sys.executable, "-m", "codegraph", "--version\r"],
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0
        assert "codegraph" in out.stdout
        assert "invalid choice" not in (out.stdout + out.stderr)
