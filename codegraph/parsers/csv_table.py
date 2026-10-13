# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: CSV / TSV parser: one section per file whose preview is a
#              summary (row and column counts, the header, the first rows),
#              so column names and sample values are searchable without the
#              index growing with the file. Bounded by design:
#                - at most _MAX_ROWS rows are read as CSV (summary + scanner
#                  text), and the scanner text stops at _MAX_SCAN_CHARS;
#                - rows are counted by scanning for newlines over at most
#                  _COUNT_BYTES bytes; past that the count is a lower bound
#                  ("1048576+ rows").
#              The delimiter is a tab for .tsv, otherwise sniffed among
#              , ; | and tab from the first few KB.

from __future__ import annotations

import csv
import logging
from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, SectionDef

_log = logging.getLogger(__name__)

_PREVIEW_CHARS = 400
_MAX_SCAN_CHARS = 200_000
_MAX_ROWS = 1_000  # rows parsed as CSV; the rest is only counted
_PREVIEW_ROWS = 3  # data rows shown in the section preview
_MAX_HEADER_CELLS = 30
_COUNT_BYTES = 64 * 1024 * 1024  # newline counting stops here
_SNIFF_BYTES = 8 * 1024


@register_parser(".csv", ".tsv")
class CsvParser(BaseParser):
    """CSV and TSV files summarized as one searchable section."""

    lang = "csv"
    extensions = [".csv", ".tsv"]
    extracts = ["sections"]
    description = "CSV / TSV header, size and first rows as a searchable section"

    def parse(self, path: Path) -> FileIndex:
        idx = FileIndex(path=str(path), lang=self.lang)
        try:
            with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
                head = fh.read(_SNIFF_BYTES)
                fh.seek(0)
                delimiter = _delimiter(path, head)
                rows: list[list[str]] = []
                reader = csv.reader(fh, delimiter=delimiter)
                for row in reader:
                    rows.append(row)
                    if len(rows) >= _MAX_ROWS:
                        break
        except (OSError, csv.Error) as exc:
            _log.warning("csv parse failed, indexed empty: %s (%s)", path, exc)
            return idx

        total, exact = _count_lines(path)
        header = [c.strip() for c in (rows[0] if rows else [])][:_MAX_HEADER_CELLS]
        data_rows = max(total - 1, 0) if exact else max(total - 1, len(rows) - 1)
        width = max((len(r) for r in rows), default=0)
        count = f"{data_rows}" if exact else f"{data_rows}+"
        parts = [f"{count} rows x {width} cols"]
        if header:
            parts.append("columns: " + ", ".join(c for c in header if c))
        for row in rows[1 : 1 + _PREVIEW_ROWS]:
            cells = [c.strip() for c in row if c.strip()]
            if cells:
                parts.append(" ; ".join(cells))
        idx.sections.append(
            SectionDef(
                id=f"{path}::table",
                title=path.name,
                level=1,
                file_path=str(path),
                start_line=1,
                end_line=max(total, 1),
                body_preview=" | ".join(parts)[:_PREVIEW_CHARS],
                anchor="table",
            )
        )

        scan: list[str] = []
        size = 0
        for row in rows:
            line = "\t".join(row)
            scan.append(line)
            size += len(line) + 1
            if size >= _MAX_SCAN_CHARS:
                break
        idx.scan_text = "\n".join(scan)[:_MAX_SCAN_CHARS]
        return idx


def _delimiter(path: Path, head: str) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(head, delimiters=",;|\t").delimiter
    except csv.Error:
        return ","


def _count_lines(path: Path) -> tuple[int, bool]:
    """(line count, exact). Reads at most _COUNT_BYTES; a last line with no
    trailing newline counts. Quoted fields with embedded newlines make this
    an over-count, which is fine for a summary."""
    lines = 0
    read = 0
    last = b"\n"
    try:
        with open(path, "rb") as fh:
            while read < _COUNT_BYTES:
                block = fh.read(min(1 << 20, _COUNT_BYTES - read))
                if not block:
                    break
                read += len(block)
                lines += block.count(b"\n")
                last = block[-1:]
            exact = not fh.read(1)
    except OSError:
        return 0, False
    if exact and last != b"\n":
        lines += 1
    return lines, exact
