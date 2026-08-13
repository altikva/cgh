# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rasterize PDF pages to PNGs so the vision pipeline (which
#              only reads images) can read a PDF's diagrams. Uses pypdfium2
#              (PDFium, BSD/Apache, pip wheels, no system binary, not AGPL
#              unlike pymupdf). Behind the cgh-vision[pdf] extra: a clear
#              error names the install when it is missing.

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path


class PdfRenderError(RuntimeError):
    """PDF rasterization could not run (missing extra) or failed."""


def _parse_pages(spec: str, total: int) -> list[int]:
    """0-based page indices from a 1-based spec: "" / "all" -> every page,
    "3" -> that page, "2-5" -> the range, "1,4,6" -> the list. Out-of-range
    entries are dropped, so a typo never crashes the run."""
    spec = (spec or "").strip().lower()
    if spec in ("", "all"):
        return list(range(total))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            out.extend(range(lo - 1, hi))
        else:
            try:
                out.append(int(part) - 1)
            except ValueError:
                continue
    return [i for i in out if 0 <= i < total]


def iter_pdf_pages(
    path: Path, pages: str = "", scale: float = 2.0
) -> Iterator[tuple[int, Path]]:
    """Yield (page_number_1_based, temp_png_path) for each requested page.
    The caller deletes each temp file. `scale` maps 72 dpi to output pixels
    (2.0 = 144 dpi, enough for diagram labels). Raises PdfRenderError with
    the install hint when pypdfium2 is absent."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise PdfRenderError(
            'reading a PDF needs the pdf extra: pip install "cgh-vision[pdf]"'
        ) from exc

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception as exc:
        raise PdfRenderError(f"cannot open pdf {path.name}: {exc}") from exc

    try:
        wanted = _parse_pages(pages, len(doc))
        for i in wanted:
            page = doc[i]
            bitmap = page.render(scale=scale)
            pil = bitmap.to_pil()
            fd, name = tempfile.mkstemp(prefix=f"cghvision-p{i + 1}-", suffix=".png")
            os.close(fd)
            pil.save(name)
            yield i + 1, Path(name)
    finally:
        doc.close()
