# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-18
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Bob IDE is a VS Code fork: it reads the repo-root .mcp.json
#              and scans .claude/skills and .claude/rules. It never reads
#              .bob/, which setup used to write, so the files sat inert.
#              An IDE also gets no login-shell PATH, so the command must be
#              an absolute path, and on Windows the windowless binary.

from __future__ import annotations

import json
import os
import shutil

from codegraph.cli.commands_init import _mcp_command


class TestMcpCommand:
    def test_resolves_an_absolute_path(self, monkeypatch):
        """A bare name never resolves from a GUI process."""
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(
            shutil, "which", lambda n: "/opt/bin/cgh" if n == "cgh" else None
        )

        command, args = _mcp_command()

        assert command == "/opt/bin/cgh"
        assert os.path.isabs(command)
        assert args[:1] == ["serve"]

    def test_windows_prefers_the_windowless_binary(self, monkeypatch):
        """cgh.exe is a console app: a GUI parent spawning it flashes a window."""
        monkeypatch.setattr(os, "name", "nt")
        found = {"cghw": r"C:\Scripts\cghw.exe", "cgh": r"C:\Scripts\cgh.exe"}
        monkeypatch.setattr(shutil, "which", lambda n: found.get(n))

        command, _args = _mcp_command()

        assert command == r"C:\Scripts\cghw.exe"

    def test_falls_back_to_the_interpreter(self, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(shutil, "which", lambda n: None)

        command, args = _mcp_command()

        assert os.path.isabs(command)
        assert args[:3] == ["-m", "codegraph", "serve"]


class TestSetupWritesTheRootMcpJson:
    def test_root_mcp_json_not_dot_bob(self, tmp_path, monkeypatch):
        from codegraph.cli.commands_init import _install_integration

        monkeypatch.setattr(
            shutil, "which", lambda n: "/opt/bin/cgh" if n == "cgh" else None
        )
        _install_integration(tmp_path, "bob")

        mcp = tmp_path / ".mcp.json"
        assert mcp.is_file(), "Bob reads the repo-root .mcp.json"
        assert not (tmp_path / ".bob" / "mcp.json").exists()

        entry = json.loads(mcp.read_text())["mcpServers"]["codegraph"]
        assert entry["command"] == "/opt/bin/cgh"
        assert entry["cwd"] == str(tmp_path.resolve())
