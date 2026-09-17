# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-16
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Config files expose their keys through the same section model as
#              Markdown headings, so anything reading sections as documentation
#              was served .mcp.json and pre-commit config alongside real docs.
#              The parser that creates a section says which kind it is.

from __future__ import annotations

import pytest

from codegraph.parsers import get_parser_for_path


def _sections(tmp_path, name: str, body: str):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    parser = get_parser_for_path(path)
    assert parser is not None, name
    return parser.parse(path).sections


def test_markdown_headings_are_documentation(tmp_path):
    sections = _sections(tmp_path, "README.md", "# Guide\n\ntext\n\n## Usage\n\nmore\n")

    assert sections
    assert {s.kind for s in sections} == {"doc"}


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("settings.yaml", "server:\n  host: localhost\n  port: 8080\n"),
        ("pyproject.toml", '[project]\nname = "x"\nversion = "1"\n'),
        ("package.json", '{"scripts": {"build": "tsc"}}'),
    ],
)
def test_config_keys_are_config(tmp_path, name, body):
    sections = _sections(tmp_path, name, body)

    assert sections
    assert {s.kind for s in sections} == {"config"}


def test_kind_defaults_to_documentation(tmp_path):
    """An older parser that never sets the field keeps producing docs."""
    from codegraph.parsers.base import SectionDef

    section = SectionDef(
        id="x", title="t", level=1, file_path="f.md", start_line=1, end_line=2
    )

    assert section.kind == "doc"
