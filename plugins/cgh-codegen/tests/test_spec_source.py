# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-20
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for cgh_codegen.cli._resolve_spec, which resolves the
#              codegen spec from --spec, --spec-file, or stdin.

from __future__ import annotations

import argparse
import io
import sys

import pytest
from rich.console import Console

pytest.importorskip("cgh_codegen")

from cgh_codegen.cli import _resolve_spec

console = Console(stderr=True)


def _ns(spec: str = "", spec_file: str = "") -> argparse.Namespace:
    """Helper to construct a Namespace with spec and spec_file arguments."""
    return argparse.Namespace(spec=spec, spec_file=spec_file)


def test_inline_spec() -> None:
    assert _resolve_spec(_ns(spec="do X"), console) == "do X"


def test_spec_file(tmp_path) -> None:
    spec_file = tmp_path / "spec.txt"
    spec_file.write_text("from file\n")
    assert _resolve_spec(_ns(spec_file=str(spec_file)), console) == "from file"


def test_stdin_via_dash(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin\n"))
    assert _resolve_spec(_ns(spec="-"), console) == "from stdin"


def test_empty_spec_exits() -> None:
    with pytest.raises(SystemExit):
        _resolve_spec(_ns(), console)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
