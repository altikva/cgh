# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: PDF parser: one section per page, with the page's extracted
#              text as the searchable preview. When the file carries an
#              outline (table of contents), top-level outline titles become
#              level-1 sections anchored to their page. "Lines" are page
#              numbers: Read cannot open a pdf at a line anyway, and page
#              anchors are what a human asks for.

from __future__ import annotations

from pathlib import Path

from codegraph.parsers.base import BaseParser, FileIndex, SectionDef

_PREVIEW_CHARS = 400


class PdfParser(BaseParser):
    """Best-effort pdf to sections. Encrypted or corrupt files yield an
    empty index, never an exception."""

    lang = "pdf"
    extensions = [".pdf"]
    extracts = ["sections"]
    description = "PDF pages and outline entries as document sections"

    def parse(self, path: Path) -> FileIndex:
        idx = FileIndex(path=str(path), lang=self.lang)
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    return idx

            outline_titles = self._outline_by_page(reader)
            for page_no, page in enumerate(reader.pages, start=1):
                try:
                    text = (page.extract_text() or "").strip()
                except Exception:
                    text = ""
                title = outline_titles.get(page_no) or f"Page {page_no}"
                idx.sections.append(
                    SectionDef(
                        id=f"{path}::page{page_no}",
                        title=title,
                        level=1 if page_no in outline_titles else 2,
                        file_path=str(path),
                        start_line=page_no,
                        end_line=page_no,
                        body_preview=text[:_PREVIEW_CHARS],
                        anchor=f"page-{page_no}",
                    )
                )
        except Exception:
            return idx
        return idx

    def _outline_by_page(self, reader) -> dict[int, str]:
        """Map page number to the first top-level outline title on it."""
        titles: dict[int, str] = {}
        try:
            for item in reader.outline:
                if isinstance(item, list):
                    continue  # nested outline levels, keep it flat
                try:
                    page_no = reader.get_destination_page_number(item) + 1
                    titles.setdefault(page_no, str(item.title))
                except Exception:
                    continue
        except Exception:
            pass
        return titles
