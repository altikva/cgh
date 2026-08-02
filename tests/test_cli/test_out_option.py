# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The shared --out contract: stdout always carries the
#              artifact, --out also writes the file (parent dirs
#              created, confirmation on stderr), and the tip only
#              shows on an interactive stderr.

from __future__ import annotations

import argparse

from codegraph.cli.output import add_out_option, emit_result


def test_option_registers_with_default_empty():
    p = argparse.ArgumentParser()
    add_out_option(p, what="the report")
    args = p.parse_args([])
    assert args.out == ""
    assert p.parse_args(["--out", "r.md"]).out == "r.md"


def test_emit_writes_file_and_confirms(tmp_path, capsys):
    target = tmp_path / "nested" / "report.md"
    emit_result("# hello", out=str(target))
    captured = capsys.readouterr()
    assert captured.out == "# hello\n"
    assert target.read_text(encoding="utf-8") == "# hello\n"
    assert "saved to" in captured.err


def test_tip_stays_out_of_pipes(capsys):
    # pytest's captured stderr is not a TTY, exactly like a pipe or CI.
    emit_result("# hello")
    captured = capsys.readouterr()
    assert captured.out == "# hello\n"
    assert captured.err == ""
