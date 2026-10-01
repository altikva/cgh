# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-01
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Claude Code on Windows runs hook commands through Git Bash,
#              which eats the backslashes of a Windows path. Hook commands
#              carry forward slashes, and init replaces a hook an older
#              version wrote with backslashes.

from __future__ import annotations

from codegraph.cli import commands_init as ci


def test_windows_launcher_path_uses_forward_slashes(monkeypatch):
    monkeypatch.setattr(ci.os, "name", "nt")
    exe = r"C:\Users\jn532e9l\AppData\Local\Python\pythoncore-3.14-64\Scripts\cghw.EXE"
    assert ci._hook_exe_path(exe) == (
        "C:/Users/jn532e9l/AppData/Local/Python/pythoncore-3.14-64/Scripts/cghw.EXE"
    )
    assert ci._hook_exe_path(r"C:\Program Files\cgh\cghw.exe") == (
        '"C:/Program Files/cgh/cghw.exe"'
    )


def test_posix_paths_are_left_alone():
    if ci.os.name == "nt":
        return
    assert ci._hook_exe_path("/Users/joy/.local/bin/cgh") == "/Users/joy/.local/bin/cgh"


def test_init_replaces_a_backslashed_hook(monkeypatch):
    spec = next(
        s
        for s in ci._claude_hook_specs("C:/x/cghw.EXE")
        if s["marker"] == "cgh-precheck-bash"
    )
    local = {}
    ci._append_hook(
        local,
        {**spec, "command": r"C:\x\cghw.EXE _hook_precheck_bash  # cgh-precheck-bash"},
    )

    result = ci._ensure_claude_hooks({}, local, "C:/x/cghw.EXE")

    commands = [
        h["hooks"][0]["command"]
        for h in local["hooks"][spec["event"]]
        if "cgh-precheck-bash" in h["hooks"][0]["command"]
    ]
    assert commands == [spec["command"]]
    assert "\\" not in commands[0]
    assert result["local_changed"]


def test_init_keeps_a_working_hook_untouched():
    spec = next(
        s
        for s in ci._claude_hook_specs("/opt/cgh")
        if s["marker"] == "cgh-precheck-bash"
    )
    custom = "cgh _hook_precheck_bash  # cgh-precheck-bash"
    local = {}
    ci._append_hook(local, {**spec, "command": custom})
    ci._ensure_claude_hooks({}, local, "/opt/cgh")
    bucket = local["hooks"][spec["event"]]
    assert [h["hooks"][0]["command"] for h in bucket if "precheck-bash" in str(h)] == [
        custom
    ]
