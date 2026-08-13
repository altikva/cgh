# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The plain-text parser claims txt/log/csv/tsv/text/rst so they
#              are indexed at all, and exposes their content as scan_text so
#              PII and secret scanners can see it.

from __future__ import annotations

from pathlib import Path

import pytest

from codegraph.parsers import get_parser_for_path


@pytest.mark.parametrize("ext", [".txt", ".text", ".log", ".csv", ".tsv", ".rst"])
def test_plaintext_extensions_are_claimed(ext):
    parser = get_parser_for_path(Path(f"file{ext}"))
    assert parser is not None
    assert type(parser).__name__ == "PlainTextParser"


def test_content_is_exposed_as_scan_text(tmp_path):
    f = tmp_path / "contacts.txt"
    f.write_text("Jean Dupont, jean.dupont@example.com, +33 6 12 34 56 78")
    idx = get_parser_for_path(f).parse(f)
    assert idx.lang == "text"
    assert "jean.dupont@example.com" in idx.scan_text
    assert idx.sections and idx.sections[0].title == "contacts.txt"


def test_unreadable_file_yields_empty_index(tmp_path):
    # A directory path is not a readable text file; parse must not raise.
    idx = get_parser_for_path(Path("d.txt")).parse(tmp_path)
    assert idx.scan_text == ""
