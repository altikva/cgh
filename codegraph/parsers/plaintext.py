# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Plain-text parser for .txt / .text / .log / .csv / .tsv /
#              .rst. The indexer skips any file no parser claims, so these
#              formats were invisible: not indexed, never scanned for PII or
#              secrets. This claims them and exposes their content as
#              scan_text so scanners see it, with a short preview section so
#              the file is findable. No symbols, no structure: it is text.

from __future__ import annotations

from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, SectionDef

_PREVIEW_CHARS = 400
_MAX_SCAN_CHARS = 200_000  # cap the text fed to scanners


@register_parser(".txt", ".text", ".log", ".csv", ".tsv", ".rst")
class PlainTextParser(BaseParser):
    """Register a plain-text file as indexed and expose its content to
    scanners. Read-with-replace: a stray non-UTF-8 byte never crashes the
    scan, it just decodes lossily."""

    lang = "text"
    extensions = [".txt", ".text", ".log", ".csv", ".tsv", ".rst"]
    extracts = ["sections"]
    description = (
        "Plain-text files (txt, log, csv, ...) indexed for search and scanning"
    )

    def parse(self, path: Path) -> FileIndex:
        idx = FileIndex(path=str(path), lang=self.lang)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return idx
        idx.scan_text = content[:_MAX_SCAN_CHARS]
        idx.sections.append(
            SectionDef(
                id=f"{path}::text",
                title=path.name,
                level=1,
                file_path=str(path),
                start_line=1,
                end_line=content.count("\n") + 1,
                body_preview=content[:_PREVIEW_CHARS],
                anchor="text",
            )
        )
        return idx
