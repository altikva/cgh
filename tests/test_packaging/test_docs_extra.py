# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-10
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The document parsers (pdf/docx/xlsx/pptx) are an opt-in `docs`
#              extra, never core dependencies, so a minimal or internet-facing
#              host that never parses a document does not inherit lxml, pillow
#              and the Office/PDF readers (weight plus CVE surface).
from __future__ import annotations

import importlib.metadata as metadata

_DOC_PACKAGES = ("pypdf", "python-docx", "openpyxl", "python-pptx")


def _requires() -> list[str]:
    return metadata.requires("cgh") or []


def test_doc_parsers_are_not_core_dependencies():
    core = [
        r
        for r in _requires()
        for pkg in _DOC_PACKAGES
        if r.startswith(pkg) and "extra ==" not in r
    ]
    assert not core, f"document parsers must not be core deps, found: {core}"


def test_doc_parsers_live_in_the_docs_extra():
    for pkg in _DOC_PACKAGES:
        in_docs = [
            r for r in _requires() if r.startswith(pkg) and 'extra == "docs"' in r
        ]
        assert in_docs, f"{pkg} must be declared in the [docs] extra"
