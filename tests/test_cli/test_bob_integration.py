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


class TestSetupWritesTheBobMcpJson:
    def test_writes_dot_bob_mcp_json(self, tmp_path, monkeypatch):
        from codegraph.cli.commands_init import _install_integration

        monkeypatch.setattr(
            shutil, "which", lambda n: "/opt/bin/cgh" if n == "cgh" else None
        )
        _install_integration(tmp_path, "bob")

        mcp = tmp_path / ".bob" / "mcp.json"
        assert mcp.is_file(), "the Bob agent reads .bob/mcp.json"

        entry = json.loads(mcp.read_text())["mcpServers"]["codegraph"]
        assert entry["command"] == "/opt/bin/cgh"
        assert entry["cwd"] == str(tmp_path.resolve())

    def test_timeout_is_in_milliseconds(self, tmp_path, monkeypatch):
        """Bob reads "timeout" in ms: 300 meant 0.3 s and cancelled every
        tool call, so the entry must carry a value Bob snaps to 2 minutes."""
        from codegraph.cli.commands_init import _install_integration

        monkeypatch.setattr(shutil, "which", lambda n: None)
        _install_integration(tmp_path, "bob")

        entry = json.loads((tmp_path / ".bob" / "mcp.json").read_text())
        assert entry["mcpServers"]["codegraph"]["timeout"] == 120_000

    def test_always_allow_covers_safe_tools_only(self, tmp_path, monkeypatch):
        from codegraph.cli.commands_init import _install_integration

        monkeypatch.setattr(shutil, "which", lambda n: None)
        _install_integration(tmp_path, "bob")

        allow = json.loads((tmp_path / ".bob" / "mcp.json").read_text())["mcpServers"][
            "codegraph"
        ]["alwaysAllow"]
        assert {"context_for_task", "architecture_overview", "resume"} <= set(allow)
        assert not {"knowledge_forget", "fetch_and_index", "add_directory"} & set(allow)

    def test_reinit_keeps_user_approvals_and_larger_timeout(
        self, tmp_path, monkeypatch
    ):
        from codegraph.cli.commands_init import _install_integration

        monkeypatch.setattr(shutil, "which", lambda n: None)
        bob = tmp_path / ".bob"
        bob.mkdir()
        (bob / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "codegraph": {
                            "timeout": 300_000,
                            "alwaysAllow": ["fetch_and_index"],
                        },
                        "other": {"command": "x"},
                    }
                }
            )
        )
        _install_integration(tmp_path, "bob")

        servers = json.loads((bob / "mcp.json").read_text())["mcpServers"]
        assert servers["other"] == {"command": "x"}
        assert servers["codegraph"]["timeout"] == 300_000
        assert "fetch_and_index" in servers["codegraph"]["alwaysAllow"]


def test_bob_always_allow_matches_registered_tools():
    """The static scan must see the same tools the server registers."""
    import asyncio

    import codegraph.server as srv
    from codegraph.cli.commands_init import _BOB_CONFIRM_TOOLS, _bob_always_allow

    # fastmcp 2.x names it get_tools (a dict keyed by name); 3.0 and later
    # expose list_tools. The supported range spans both.
    if hasattr(srv.mcp, "list_tools"):
        registered = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    else:
        registered = set(asyncio.run(srv.mcp.get_tools()))
    assert set(_bob_always_allow()) == registered - _BOB_CONFIRM_TOOLS
