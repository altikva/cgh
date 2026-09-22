# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Zombie liveness detection for process monitoring via /proc/stat.

from __future__ import annotations

from codegraph.state.pidfile import _parse_proc_stat_state, process_alive


class TestParseState:
    def test_parse_reads_state_char(self):
        result = _parse_proc_stat_state(b"123 (proc) R 1 0")
        assert result == "R"

    def test_parse_handles_parens_in_comm(self):
        # When the command name contains parens, the parser scans from the
        # LAST close paren to find the state field, not the first.
        result = _parse_proc_stat_state(b"123 (weird ) (name) Z 1 0")
        assert result == "Z"

    def test_parse_returns_none_on_garbage(self):
        result = _parse_proc_stat_state(b"no parens here")
        assert result is None


class TestProcessAlive:
    def test_zombie_is_dead(self, monkeypatch):
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        monkeypatch.setattr("codegraph.state.pidfile._process_state", lambda pid: "Z")
        assert process_alive(4242) is False

    def test_running_is_alive(self, monkeypatch):
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        monkeypatch.setattr("codegraph.state.pidfile._process_state", lambda pid: "S")
        assert process_alive(4242) is True

    def test_missing_pid_is_dead(self, monkeypatch):
        def raise_lookup_error(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr("os.kill", raise_lookup_error)
        assert process_alive(4242) is False
