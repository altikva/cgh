# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-04
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Hooks fire on every tool call; on Windows a console
#              launcher flashes a window each time. The wiring swaps in
#              the windowless launcher there, and only there, and only
#              when it exists.

from __future__ import annotations

import shutil

from codegraph.cli.commands_init import _hook_launcher


def test_posix_is_left_alone(monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    assert _hook_launcher("cgh") == "cgh"


def test_windows_swaps_in_the_windowless_launcher(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\tools\cghw.exe")
    assert _hook_launcher("cgh") == r"C:\tools\cghw.exe"


def test_windows_without_the_launcher_keeps_the_console_one(monkeypatch):
    """An install predating gui-scripts must keep working."""
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _hook_launcher("cgh") == "cgh"


def test_python_module_invocation_is_untouched(monkeypatch):
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr(shutil, "which", lambda name: r"C:\tools\cghw.exe")
    prefix = r"C:\python.exe -m codegraph"
    assert _hook_launcher(prefix) == prefix
