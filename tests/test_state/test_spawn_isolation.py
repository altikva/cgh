# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh starts Python processes from inside the indexed repo (the
#              owner, and the MCP server and hooks `cgh init` writes when no
#              cgh binary is on PATH). A repo with its own top-level
#              `codegraph/` package must not be imported in place of the
#              installed cgh, so every `python -m codegraph` runs with -P.

from __future__ import annotations

import subprocess
import sys

from codegraph.cli import commands_init
from codegraph.state import ipc


def _shadow_repo(tmp_path):
    pkg = tmp_path / "codegraph"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text("print('SHADOW')\n", encoding="utf-8")
    return tmp_path


def test_python_minus_p_ignores_a_shadowing_package(tmp_path):
    repo = _shadow_repo(tmp_path)
    unsafe = subprocess.run(
        [sys.executable, "-m", "codegraph", "--version"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "SHADOW" in unsafe.stdout  # the hazard this guards against
    safe = subprocess.run(
        [sys.executable, "-P", "-m", "codegraph", "--version"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "SHADOW" not in safe.stdout
    assert "codegraph" in (safe.stdout + safe.stderr)


def test_spawn_owner_runs_python_with_minus_p(tmp_path, monkeypatch):
    seen: list[list[str]] = []

    class _Popen:
        def __init__(self, cmd, **_kw):
            seen.append(cmd)

    monkeypatch.setattr(ipc.subprocess, "Popen", _Popen)
    monkeypatch.setattr(ipc, "_await_owner_port", lambda *_a: None)
    monkeypatch.delattr(sys, "frozen", raising=False)
    ipc.spawn_owner(tmp_path, watch=False, reindex=False)
    assert seen and seen[0][:4] == [sys.executable, "-P", "-m", "codegraph"]


def test_generated_python_fallback_uses_minus_p(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    command, args = commands_init._mcp_command()
    assert command == sys.executable
    assert args[:3] == ["-P", "-m", "codegraph"]
