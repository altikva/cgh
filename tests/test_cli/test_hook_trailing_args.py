# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: A hook command ignores what follows it. The commands cgh
#              writes into an agent's settings end with a `# marker`
#              comment; a host without a POSIX shell passes it as arguments,
#              and an argparse exit 2 blocks the event in Bob.

from __future__ import annotations

import io
import sys

import pytest

import codegraph.__main__ as cli


@pytest.mark.parametrize(
    "argv",
    [
        ["_bob_prompt", "#", "cgh-bob-prompt"],
        ["_bob_stop", "#", "cgh-bob-precompact"],
        ["_bob_session_start", "#", "cgh-bob-start"],
        ["_bob_tool_log", "#", "cgh-bob-log"],
        ["_hook_resume_header", "#", "cgh-resume-header"],
        ["_hook_checkpoint", "#", "cgh-auto-checkpoint"],
    ],
)
def test_hook_command_survives_its_marker_as_arguments(argv, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["cgh", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    try:
        cli.main()
    except SystemExit as exc:  # a clean exit is fine, a usage error is not
        assert exc.code in (0, None)


def test_other_commands_still_reject_unknown_arguments(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["cgh", "stop", "#", "stray"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
