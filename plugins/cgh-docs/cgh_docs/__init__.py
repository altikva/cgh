# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh plugin entry point: registers the pdf, docx and xlsx
#              parsers so document files land in the graph and the FTS
#              as searchable sections.

from __future__ import annotations

CGH_PLUGIN_API = 1


def register(api) -> None:
    from .docx_parser import DocxParser
    from .pdf_parser import PdfParser
    from .xlsx_parser import XlsxParser

    api.register_parser(".pdf")(PdfParser)
    api.register_parser(".docx")(DocxParser)
    api.register_parser(".xlsx", ".xlsm")(XlsxParser)
