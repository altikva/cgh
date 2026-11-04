# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-04
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: _resolve_cgh must make a relative binary path absolute. The
#              smoke test runs `cgh index`/`search` with cwd set to a throwaway
#              repo, and on POSIX a relative executable resolves against the
#              child's cwd (the repo), not the parent's, so a path like
#              "./out/cgh-macos-arm64" is not found there; Windows resolved
#              against the parent, which is why only the Unix binary legs of
#              the release failed. A bare command name stays untouched for PATH.

from __future__ import annotations

import importlib.util
from pathlib import Path

_SMOKE = Path(__file__).resolve().parents[2] / "packaging" / "smoke_documents.py"
_spec = importlib.util.spec_from_file_location("smoke_documents", _SMOKE)
assert _spec and _spec.loader
smoke_documents = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_documents)


def test_relative_path_to_existing_binary_becomes_absolute(tmp_path, monkeypatch):
    binary = tmp_path / "out" / "cgh-macos-arm64"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    # Call it by a relative path from tmp_path, as the workflow does ("./out/..").
    monkeypatch.chdir(tmp_path)
    resolved = smoke_documents._resolve_cgh("./out/cgh-macos-arm64")
    assert Path(resolved).is_absolute()
    assert Path(resolved) == binary.resolve()


def test_bare_command_name_is_left_for_path_lookup(tmp_path, monkeypatch):
    # A name with no path separator and no file in cwd must pass through
    # unchanged so the child resolves it against PATH.
    monkeypatch.chdir(tmp_path)
    assert smoke_documents._resolve_cgh("cgh") == "cgh"


def test_missing_relative_path_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert smoke_documents._resolve_cgh("./out/absent") == "./out/absent"
