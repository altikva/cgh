# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (http://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Regex-based parser for Markdown files (plugin architecture).
#              Extracts headings (sections), internal links, and code block references.
#              No tree-sitter dependency: pure Python regex.

from __future__ import annotations

import re
from pathlib import Path

from . import register_parser
from .base import BaseParser, CodeRef, FileIndex, LinkRef, SectionDef

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#*)?$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_FENCED_START_RE = re.compile(r"^```(\w*)")
_FENCED_END_RE = re.compile(r"^```\s*$")

# Patterns that look like code symbols (PascalCase, snake_case, dotted.path)
_SYMBOL_RE = re.compile(
    r"(?:[A-Z][a-zA-Z0-9]+(?:[A-Z][a-z]+)+)"  # PascalCase (2+ words)
    r"|(?:[a-z_][a-z0-9_]*(?:_[a-z0-9]+)+)"  # snake_case (2+ parts)
    r"|(?:[a-z_]\w+\.\w+)"  # dotted.path
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    """GitHub-style heading anchor: lowercase, strip non-alnum, dashes for spaces."""
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@register_parser(".md", ".mdx")
class MarkdownParser(BaseParser):
    """Regex-based Markdown/MDX parser — sections, links, code references."""

    lang = "markdown"
    extensions = [".md", ".mdx"]
    extracts = ["sections", "links", "code_refs"]
    description = "Regex-based Markdown/MDX parser"

    def parse(self, path: Path) -> FileIndex:
        path_str = str(path)
        text = Path(path_str).read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        index = FileIndex(path=path_str, lang=self.lang)
        headings: list[tuple[int, int, str]] = []  # (line_no, level, title)

        in_fenced = False

        for line_no, line in enumerate(lines, start=1):
            # Track fenced code blocks to avoid false heading matches
            if _FENCED_START_RE.match(line.strip()) and not in_fenced:
                in_fenced = True
                # Extract symbol-like tokens from fenced blocks
                continue
            if in_fenced:
                if _FENCED_END_RE.match(line.strip()):
                    in_fenced = False
                else:
                    # Look for code symbols inside fenced blocks
                    for m in _SYMBOL_RE.finditer(line):
                        index.code_refs.append(
                            CodeRef(
                                symbol=m.group(0),
                                line=line_no,
                                context="fenced",
                            )
                        )
                continue

            # Headings
            hm = _HEADING_RE.match(line)
            if hm:
                level = len(hm.group(1))
                title = hm.group(2).strip()
                headings.append((line_no, level, title))

            # Links
            for lm in _LINK_RE.finditer(line):
                index.links.append(
                    LinkRef(
                        label=lm.group(1),
                        target=lm.group(2),
                        line=line_no,
                    )
                )

            # Inline code symbols
            for cm in _INLINE_CODE_RE.finditer(line):
                code = cm.group(1)
                for sm in _SYMBOL_RE.finditer(code):
                    index.code_refs.append(
                        CodeRef(
                            symbol=sm.group(0),
                            line=line_no,
                            context="inline",
                        )
                    )

        # Build sections from headings — each section spans until the next
        # heading of same or higher level (or EOF)
        total_lines = len(lines)
        for i, (line_no, level, title) in enumerate(headings):
            # Find end line: next heading of same or higher level, or EOF
            end_line = total_lines
            for j in range(i + 1, len(headings)):
                if headings[j][1] <= level:
                    end_line = headings[j][0] - 1
                    break

            # Build body preview from lines after heading until end
            body_start = line_no  # 1-based, heading is at line_no
            body_lines = lines[body_start:end_line]  # 0-based slicing
            body_text = "\n".join(body_lines).strip()
            # Collapse whitespace for preview
            body_preview = re.sub(r"\s+", " ", body_text)[:300]

            slug = _slugify(title)
            section_id = f"{path_str}::{slug}"

            # Handle duplicate slugs (e.g., multiple "## Example" headings)
            existing_ids = {s.id for s in index.sections}
            if section_id in existing_ids:
                section_id = f"{path_str}::{slug}-L{line_no}"

            index.sections.append(
                SectionDef(
                    id=section_id,
                    title=title,
                    level=level,
                    file_path=path_str,
                    start_line=line_no,
                    end_line=end_line,
                    body_preview=body_preview,
                    anchor=slug,
                )
            )

        return index
