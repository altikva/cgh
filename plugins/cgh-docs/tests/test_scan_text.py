# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Doc parsers expose their extracted text as idx.scan_text so
#              scanners see real cell / page / table content, not the raw
#              binary. Covers the xlsx data-row extraction (the header-only
#              gap that hid PII) and docx table-cell extraction.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_docs")


def test_xlsx_scan_text_includes_data_cells(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from cgh_docs.xlsx_parser import XlsxParser

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "email"])  # header
    ws.append(["Alice Martin", "alice.martin@example.com"])  # data row with PII
    path = tmp_path / "people.xlsx"
    wb.save(str(path))

    idx = XlsxParser().parse(path)
    # The header used to be all that was indexed; the data cells must now
    # be in scan_text so a PII scanner can see them.
    assert "alice.martin@example.com" in idx.scan_text
    assert "Alice Martin" in idx.scan_text


def test_docx_scan_text_includes_table_cells(tmp_path):
    docx = pytest.importorskip("docx")
    from cgh_docs.docx_parser import DocxParser

    document = docx.Document()
    document.add_paragraph("Contact list")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Bob Durand"
    table.rows[0].cells[1].text = "+33 6 11 22 33 44"
    path = tmp_path / "contacts.docx"
    document.save(str(path))

    idx = DocxParser().parse(path)
    assert "Bob Durand" in idx.scan_text
    assert "+33 6 11 22 33 44" in idx.scan_text
