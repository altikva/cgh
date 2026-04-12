"""Tests for the Markdown parser."""

from codegraph.parsers import get_parser
from codegraph.parsers.base import FileIndex


class TestMarkdownParser:
    def test_parser_exists(self):
        parser = get_parser(".md")
        assert parser is not None
        assert parser.lang == "markdown"

    def test_mdx_supported(self):
        assert get_parser(".mdx") is not None

    def test_parse_sections(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)
        assert isinstance(idx, FileIndex)

        titles = [s.title for s in idx.sections]
        assert "Project Overview" in titles
        assert "Architecture" in titles
        assert "Components" in titles
        assert "API Reference" in titles

    def test_section_levels(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)

        overview = next(s for s in idx.sections if s.title == "Project Overview")
        assert overview.level == 1

        arch = next(s for s in idx.sections if s.title == "Architecture")
        assert arch.level == 2

        components = next(s for s in idx.sections if s.title == "Components")
        assert components.level == 3

    def test_section_anchors(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)

        arch = next(s for s in idx.sections if s.title == "Architecture")
        assert arch.anchor == "architecture"

    def test_body_preview(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)

        arch = next(s for s in idx.sections if s.title == "Architecture")
        assert "4-layer" in arch.body_preview

    def test_internal_links(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)

        targets = [lnk.target for lnk in idx.links]
        assert any("SETUP.md" in t for t in targets)

    def test_code_refs(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)

        symbols = [r.symbol for r in idx.code_refs]
        assert "DonationHandler" in symbols

    def test_line_numbers(self, sample_markdown):
        parser = get_parser(".md")
        idx = parser.parse(sample_markdown)

        for sec in idx.sections:
            assert sec.start_line > 0
            assert sec.end_line >= sec.start_line
