# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh files --check` explains why a file is not indexed using
#              the same decision the indexer makes: no parser for the
#              suffix, over the size cap, missing, or a directory.

from __future__ import annotations

from codegraph.cli.commands_files import _index_decision


def test_missing_file(tmp_path):
    ok, reason = _index_decision(tmp_path / "nope.py", tmp_path)
    assert ok is False and "does not exist" in reason


def test_directory(tmp_path):
    ok, reason = _index_decision(tmp_path, tmp_path)
    assert ok is False and "directory" in reason


def test_no_parser_for_suffix(tmp_path):
    f = tmp_path / "data.lock"
    f.write_text("x")
    ok, reason = _index_decision(f, tmp_path)
    assert ok is False and "no parser" in reason


def test_indexable_source_file(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def f(): pass\n")
    ok, reason = _index_decision(f, tmp_path)
    assert ok is True and "indexable" in reason


def test_over_size_cap(tmp_path, monkeypatch):
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 100)

    class _Cfg:
        ignore_patterns: list[str] = []
        max_file_size_kb = 0  # everything is "too big"

    monkeypatch.setattr(
        "codegraph.core.config.load_config", lambda root: _Cfg(), raising=True
    )
    ok, reason = _index_decision(f, tmp_path)
    assert ok is False and "max_file_size_kb" in reason
