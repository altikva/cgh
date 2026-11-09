# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Document parsers for pdf, docx, xlsx and pptx. Each file
#              becomes searchable sections, exactly like Markdown headings,
#              so search_docs, doc_outline, fts_search and the federated
#              fan-out work on them unchanged. The extracted text is also
#              exposed as idx.scan_text so scanners see page / cell / slide
#              content rather than the raw zip or pdf bytes.
#
#              Parsing is best effort: an encrypted pdf or a corrupt workbook
#              yields an empty index and one log line, never a failed scan.
#              The reading libraries are imported lazily inside parse(), so
#              a build that lacks one of them (a trimmed binary, a broken
#              lxml wheel) keeps every parser registered and skips that format
#              with a single log line per process instead of crashing.
#
#              Resource bounds sit BEFORE the expensive step, never after it:
#                - every file must be a regular file under _MAX_INPUT_BYTES,
#                  whatever max_file_size_kb says (a forced index skips that
#                  cap, and a device or fifo has no meaningful size);
#                - docx, xlsx and pptx are zip containers, and python-docx /
#                  python-pptx load every part on open: the zip directory is
#                  checked first (member count, total uncompressed size,
#                  per-member compression ratio) and a container over any cap
#                  is skipped without the library ever seeing it. zipfile
#                  stops inflating a member at its declared size, so a member
#                  that lies about its size cannot inflate past the check;
#                - pdf: the page count is read before any text is extracted,
#                  only the first _MAX_PDF_PAGES pages are touched, each page's
#                  text is cut at _MAX_PAGE_CHARS and extraction stops once
#                  _MAX_PDF_TEXT_CHARS were extracted; pypdf >= 6 itself caps
#                  how far one compressed stream may inflate;
#                - xlsx streams rows (read_only) under a row and a cell budget
#                  and never trusts the sheet's declared dimensions for work;
#                - pptx walks slides lazily, under a slide and a shape budget,
#                  with a nesting limit on group shapes;
#                - docx walks at most _MAX_DOCX_PARAGRAPHS paragraphs and
#                  _MAX_DOCX_TABLE_ROWS table rows.

from __future__ import annotations

import logging
import os
import re
import stat
import warnings
import zipfile
from pathlib import Path

from . import register_parser
from .base import BaseParser, FileIndex, SectionDef

_log = logging.getLogger(__name__)

_PREVIEW_CHARS = 400  # what lands in the FTS per section
_MAX_SCAN_CHARS = 200_000  # extracted text handed to scanners, per file

# Input and container caps, checked before any library opens the file.
_MAX_INPUT_BYTES = 256 * 1024 * 1024  # on-disk size, any format
_MAX_ZIP_MEMBERS = 2_000
_MAX_ZIP_UNCOMPRESSED = 64 * 1024 * 1024  # sum of declared member sizes
_MAX_ZIP_RATIO = 100  # per member, uncompressed / compressed
_ZIP_RATIO_FLOOR = 1024 * 1024  # members under 1 MB are exempt from the ratio

# Work budgets, checked inside each walk.
_MAX_PDF_PAGES = 500
_MAX_PAGE_CHARS = 20_000  # extracted text kept per pdf page
_MAX_PDF_TEXT_CHARS = 2_000_000  # extraction stops past this, per file
_MAX_PDF_OUTLINE_ITEMS = 1_000
_MAX_DOCX_PARAGRAPHS = 50_000
_MAX_DOCX_TABLE_ROWS = 20_000
_MAX_XLSX_SHEETS = 200
_MAX_XLSX_ROWS = 50_000  # rows walked per workbook, empty rows included
_MAX_XLSX_CELLS = 1_000_000  # cells walked per workbook, empty cells included
_MAX_HEADER_CELLS = 30
_MAX_SLIDES = 500
_MAX_PPTX_SHAPES = 20_000  # shapes walked per deck, group members included
_MAX_GROUP_DEPTH = 8
_HEADING_RE = re.compile(r"heading\s*(\d)", re.IGNORECASE)

_missing_logged: set[str] = set()


def _missing_dependency(fmt: str, module: str, exc: BaseException) -> None:
    """Log once per process that a format's reader is not importable."""
    if fmt in _missing_logged:
        return
    _missing_logged.add(fmt)
    _log.warning(
        "%s files skipped: %s is not importable in this install (%s)",
        fmt,
        module,
        exc,
    )


def _input_ok(path: Path, fmt: str) -> bool:
    """A regular file under the hard input cap. Checked before any read."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        _log.warning("%s skipped, not a regular file: %s", fmt, path)
        return False
    if st.st_size > _MAX_INPUT_BYTES:
        _log.warning(
            "%s skipped, %d bytes over the %d byte input cap: %s",
            fmt,
            st.st_size,
            _MAX_INPUT_BYTES,
            path,
        )
        return False
    return True


def zip_container_ok(path: Path, fmt: str) -> bool:
    """Check a zip container's directory before any library opens it.

    Reads the central directory only (no member is inflated) and rejects
    the file, with one log line, when it has more than _MAX_ZIP_MEMBERS
    members, when the declared uncompressed sizes add up to more than
    _MAX_ZIP_UNCOMPRESSED, or when a member of at least _ZIP_RATIO_FLOOR
    bytes compresses better than _MAX_ZIP_RATIO:1, the signature of a
    decompression bomb. A file that is not a zip is rejected too: the
    library would only fail on it later.
    """
    if not _input_ok(path, fmt):
        return False
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        _log.warning("%s parse failed, not a zip container: %s", fmt, path)
        return False
    if len(infos) > _MAX_ZIP_MEMBERS:
        _log.warning(
            "%s skipped, %d zip members over the %d cap: %s",
            fmt,
            len(infos),
            _MAX_ZIP_MEMBERS,
            path,
        )
        return False
    total = 0
    for info in infos:
        total += info.file_size
        if total > _MAX_ZIP_UNCOMPRESSED:
            _log.warning(
                "%s skipped, inflates past the %d byte cap: %s",
                fmt,
                _MAX_ZIP_UNCOMPRESSED,
                path,
            )
            return False
        if info.file_size >= _ZIP_RATIO_FLOOR and (
            info.file_size > _MAX_ZIP_RATIO * max(info.compress_size, 1)
        ):
            _log.warning(
                "%s skipped, member %s compresses over %d:1: %s",
                fmt,
                info.filename,
                _MAX_ZIP_RATIO,
                path,
            )
            return False
    return True


class _ScanText:
    """Bounded accumulator for the scanner text of one file."""

    def __init__(self, limit: int = _MAX_SCAN_CHARS) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.limit = limit

    @property
    def full(self) -> bool:
        return self.size >= self.limit

    def add(self, text: str) -> None:
        if text and not self.full:
            text = text[: self.limit - self.size]
            self.parts.append(text)
            self.size += len(text) + 1

    def value(self) -> str:
        return "\n".join(self.parts)[: self.limit]


class _Preview:
    """Bounded accumulator for a section preview: stops keeping text once
    the preview is full instead of joining everything and cutting."""

    def __init__(self, limit: int = _PREVIEW_CHARS) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.limit = limit

    def add(self, text: str) -> None:
        if text and self.size < self.limit:
            self.parts.append(text[: self.limit])
            self.size += len(text) + 1

    def value(self) -> str:
        return " ".join(self.parts)[: self.limit]


# ---------------------------------------------------------------------------
# pdf
# ---------------------------------------------------------------------------


@register_parser(".pdf")
class PdfParser(BaseParser):
    """PDF pages and outline entries as document sections."""

    lang = "pdf"
    extensions = [".pdf"]
    extracts = ["sections"]
    description = "PDF pages and outline entries as document sections"

    def parse(self, path: Path) -> FileIndex:
        # One section per page with the page text as preview. Top-level
        # outline titles name the page they point at. "Lines" are page
        # numbers: Read cannot open a pdf at a line, and a page is what a
        # human asks for.
        idx = FileIndex(path=str(path), lang=self.lang)
        if not _input_ok(path, "pdf"):
            return idx
        try:
            from pypdf import PdfReader
        except Exception as exc:
            _missing_dependency("pdf", "pypdf", exc)
            return idx
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    return idx

            # The page count comes first: it decides how many pages are
            # touched at all, before any text is extracted.
            total = len(reader.pages)
            pages = min(total, _MAX_PDF_PAGES)
            if total > pages:
                _log.info(
                    "pdf %s: %d pages, only the first %d indexed", path, total, pages
                )
            outline_titles = _pdf_outline_by_page(reader)
            scan = _ScanText()
            extracted = 0
            for page_no in range(1, pages + 1):
                text = ""
                if extracted < _MAX_PDF_TEXT_CHARS:
                    try:
                        raw = reader.pages[page_no - 1].extract_text() or ""
                    except Exception:
                        raw = ""
                    text = raw[:_MAX_PAGE_CHARS].strip()
                    extracted += len(text)
                scan.add(text)
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
            idx.scan_text = scan.value()
        except Exception:
            _log.warning("pdf parse failed, indexed empty: %s", path)
            return FileIndex(path=str(path), lang=self.lang)
        return idx


def _pdf_outline_by_page(reader) -> dict[int, str]:
    """Map page number to the first top-level outline title on it, over at
    most _MAX_PDF_OUTLINE_ITEMS entries."""
    titles: dict[int, str] = {}
    try:
        for n, item in enumerate(reader.outline):
            if n >= _MAX_PDF_OUTLINE_ITEMS:
                break
            if isinstance(item, list):
                continue  # nested outline levels, keep it flat
            try:
                page_no = reader.get_destination_page_number(item) + 1
                titles.setdefault(page_no, str(item.title)[:_PREVIEW_CHARS])
            except Exception:
                continue
    except Exception:
        pass
    return titles


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------


@register_parser(".docx")
class DocxParser(BaseParser):
    """Word headings and body text as document sections."""

    lang = "docx"
    extensions = [".docx"]
    extracts = ["sections"]
    description = "Word headings and body text as document sections"

    def parse(self, path: Path) -> FileIndex:
        # Heading-styled paragraphs become the section tree (mirroring the
        # markdown parser); the body text between headings is each
        # section's preview. "Lines" are paragraph indexes, the closest
        # stable anchor a docx has.
        idx = FileIndex(path=str(path), lang=self.lang)
        if not zip_container_ok(path, "docx"):
            return idx
        try:
            import docx
        except Exception as exc:
            _missing_dependency("docx", "python-docx", exc)
            return idx
        try:
            document = docx.Document(str(path))
        except Exception:
            _log.warning("docx parse failed, indexed empty: %s", path)
            return idx

        current: SectionDef | None = None
        body = _Preview()
        preamble = _Preview()
        scan = _ScanText()
        last = 0

        for i, para in enumerate(document.paragraphs, start=1):
            if i > _MAX_DOCX_PARAGRAPHS:
                break
            last = i
            text = (para.text or "").strip()
            scan.add(text)
            style = getattr(getattr(para, "style", None), "name", "") or ""
            m = _HEADING_RE.search(style)
            if m and text:
                if current is not None:
                    current.body_preview = body.value()
                    current.end_line = i - 1
                current = SectionDef(
                    id=f"{path}::para{i}",
                    title=text[:_PREVIEW_CHARS],
                    level=int(m.group(1)),
                    file_path=str(path),
                    start_line=i,
                    end_line=i,
                    anchor=f"para-{i}",
                )
                idx.sections.append(current)
                body = _Preview()
            elif text:
                (body if current is not None else preamble).add(text)

        if current is not None:
            current.body_preview = body.value()
            current.end_line = last

        # Table cells too: data often sits in tables, which the paragraph
        # walk above never visits.
        tables = _Preview()
        rows = 0
        try:
            for table in document.tables:
                for trow in table.rows:
                    rows += 1
                    if rows > _MAX_DOCX_TABLE_ROWS or scan.full:
                        break
                    cells = [
                        c.text.strip() for c in trow.cells if (c.text or "").strip()
                    ]
                    if cells:
                        scan.add("\t".join(cells))
                        tables.add(" | ".join(cells))
                if rows > _MAX_DOCX_TABLE_ROWS or scan.full:
                    break
        except Exception:
            pass
        idx.scan_text = scan.value()

        # A document with no heading styles still gets one section so its
        # text (or, failing any prose, its tables) is searchable.
        fallback = preamble.value() or tables.value()
        if not idx.sections and fallback:
            idx.sections.append(
                SectionDef(
                    id=f"{path}::body",
                    title=path.stem,
                    level=1,
                    file_path=str(path),
                    start_line=1,
                    end_line=max(last, 1),
                    body_preview=fallback,
                    anchor="body",
                )
            )
        return idx


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------


@register_parser(".xlsx", ".xlsm")
class XlsxParser(BaseParser):
    """Excel sheets with their header row as document sections."""

    lang = "xlsx"
    extensions = [".xlsx", ".xlsm"]
    extracts = ["sections"]
    description = "Excel sheets with their header row as document sections"

    def parse(self, path: Path) -> FileIndex:
        # One section per sheet: the rows walked and the header row in the
        # preview so column names are searchable. Read-only streaming, values
        # only, under a row and a cell budget for the whole workbook. The
        # sheet's declared dimensions are never used for work: they are
        # whatever the file says.
        idx = FileIndex(path=str(path), lang=self.lang)
        if not zip_container_ok(path, "xlsx"):
            return idx
        try:
            import openpyxl
        except Exception as exc:
            _missing_dependency("xlsx", "openpyxl", exc)
            return idx
        wb = None
        try:
            with warnings.catch_warnings():
                # "Workbook contains no default style": cosmetic, and it
                # lands on the owner's stderr for every such file.
                warnings.simplefilter("ignore", UserWarning)
                wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            scan = _ScanText()
            rows_left = _MAX_XLSX_ROWS
            cells_left = _MAX_XLSX_CELLS
            for sheet_no, ws in enumerate(wb.worksheets, start=1):
                if sheet_no > _MAX_XLSX_SHEETS:
                    break
                header: list[str] = []
                rows = 0
                width = 0
                complete = False
                if rows_left > 0 and cells_left > 0:
                    scan.add(f"# {ws.title}")
                    try:
                        complete = True
                        for row in ws.iter_rows(values_only=True):
                            if rows_left <= 0 or cells_left <= 0:
                                complete = False
                                break
                            rows_left -= 1
                            cells_left -= len(row)
                            rows += 1
                            width = max(width, len(row))
                            if rows == 1:
                                header = [
                                    str(c)[:100]
                                    for c in row[:_MAX_HEADER_CELLS]
                                    if c is not None
                                ]
                            if not scan.full:
                                cells = [str(c) for c in row if c is not None]
                                if cells:
                                    scan.add("\t".join(cells))
                    except Exception:
                        complete = False
                if rows or complete:
                    size = f"{rows}{'' if complete else '+'} rows x {width} cols"
                else:
                    size = "size not read (workbook budget spent)"
                preview = size + (f" | columns: {', '.join(header)}" if header else "")
                idx.sections.append(
                    SectionDef(
                        id=f"{path}::sheet{sheet_no}",
                        title=str(ws.title),
                        level=1,
                        file_path=str(path),
                        start_line=sheet_no,
                        end_line=sheet_no,
                        body_preview=preview[:_PREVIEW_CHARS],
                        anchor=f"sheet-{sheet_no}",
                    )
                )
            idx.scan_text = scan.value()
        except Exception:
            _log.warning("xlsx parse failed, indexed empty: %s", path)
            return FileIndex(path=str(path), lang=self.lang)
        finally:
            try:
                if wb is not None:
                    wb.close()
            except Exception:
                pass  # best-effort close of a read-only handle
        return idx


# ---------------------------------------------------------------------------
# pptx
# ---------------------------------------------------------------------------


@register_parser(".pptx")
class PptxParser(BaseParser):
    """PowerPoint slides, speaker notes and tables as document sections."""

    lang = "pptx"
    extensions = [".pptx"]
    extracts = ["sections"]
    description = "PowerPoint slides, speaker notes and tables as document sections"

    def parse(self, path: Path) -> FileIndex:
        # One level-1 section per slide, titled by the slide's title
        # placeholder (else "Slide N"), with its text frames and table
        # rows as the preview. Speaker notes get a level-2 section of their
        # own so they stay searchable even when the slide text is long.
        # "Lines" are slide numbers.
        idx = FileIndex(path=str(path), lang=self.lang)
        if not zip_container_ok(path, "pptx"):
            return idx
        try:
            from pptx import Presentation
        except Exception as exc:
            _missing_dependency("pptx", "python-pptx", exc)
            return idx
        try:
            prs = Presentation(str(path))
            scan = _ScanText()
            budget = _ShapeBudget(_MAX_PPTX_SHAPES)
            for slide_no, slide in enumerate(prs.slides, start=1):
                if slide_no > _MAX_SLIDES:
                    _log.info(
                        "pptx %s: more than %d slides, the rest is skipped",
                        path,
                        _MAX_SLIDES,
                    )
                    break
                title, lines = _pptx_slide_text(slide, budget)
                notes = _pptx_notes(slide)
                scan.add(f"# Slide {slide_no}")
                preview = _Preview()
                for line in lines:
                    scan.add(line)
                    preview.add(line)
                idx.sections.append(
                    SectionDef(
                        id=f"{path}::slide{slide_no}",
                        title=title or f"Slide {slide_no}",
                        level=1,
                        file_path=str(path),
                        start_line=slide_no,
                        end_line=slide_no,
                        body_preview=preview.value(),
                        anchor=f"slide-{slide_no}",
                    )
                )
                if notes:
                    scan.add(notes)
                    idx.sections.append(
                        SectionDef(
                            id=f"{path}::slide{slide_no}-notes",
                            title=f"Notes: {title or f'Slide {slide_no}'}",
                            level=2,
                            file_path=str(path),
                            start_line=slide_no,
                            end_line=slide_no,
                            body_preview=notes[:_PREVIEW_CHARS],
                            anchor=f"slide-{slide_no}-notes",
                        )
                    )
            idx.scan_text = scan.value()
        except Exception:
            _log.warning("pptx parse failed, indexed empty: %s", path)
            return FileIndex(path=str(path), lang=self.lang)
        return idx


class _ShapeBudget:
    """Shapes left to walk across one deck."""

    def __init__(self, limit: int) -> None:
        self.left = limit

    def take(self) -> bool:
        self.left -= 1
        return self.left >= 0


def _pptx_slide_text(slide, budget: _ShapeBudget) -> tuple[str, list[str]]:
    """The slide's title and its other text, one entry per paragraph or
    table row, in shape order. Group shapes are walked down to
    _MAX_GROUP_DEPTH; every shape visited spends the deck's budget."""
    title = ""
    try:
        if slide.shapes.title is not None:
            title = (slide.shapes.title.text_frame.text or "").strip()
            title = title[:_PREVIEW_CHARS]
    except Exception:
        title = ""
    lines: list[str] = []

    def _walk(shapes, depth: int) -> None:
        for shape in shapes:
            if not budget.take():
                return
            try:
                if getattr(shape, "shape_type", None) == 6:  # MSO_SHAPE_TYPE.GROUP
                    if depth < _MAX_GROUP_DEPTH:
                        _walk(shape.shapes, depth + 1)
                    continue
                if getattr(shape, "has_table", False) and shape.has_table:
                    for row in shape.table.rows:
                        cells = [
                            (c.text or "").strip()
                            for c in row.cells
                            if (c.text or "").strip()
                        ]
                        if cells:
                            lines.append(" | ".join(cells))
                    continue
                if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = "".join(r.text for r in para.runs).strip()
                        if text and text != title:
                            lines.append(text)
            except Exception:
                continue

    _walk(slide.shapes, 0)
    return title, lines


def _pptx_notes(slide) -> str:
    try:
        if not slide.has_notes_slide:
            return ""
        frame = slide.notes_slide.notes_text_frame
        if frame is None:
            return ""
        return " ".join((frame.text or "")[:_MAX_PAGE_CHARS].split())
    except Exception:
        return ""
