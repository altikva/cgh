# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh vision` on a PDF: pages are rasterized and run through
#              the vision pipeline per page, and a non-image / non-pdf input
#              fails fast with a clear message instead of a Pillow crash.

from __future__ import annotations

import types
from pathlib import Path

import pytest

pytest.importorskip("cgh_vision")

from cgh_vision import cli
from cgh_vision.pdf_render import _parse_pages


def test_parse_pages_spec():
    assert _parse_pages("", 3) == [0, 1, 2]
    assert _parse_pages("all", 3) == [0, 1, 2]
    assert _parse_pages("2-3", 5) == [1, 2]
    assert _parse_pages("1,4", 5) == [0, 3]
    assert _parse_pages("9", 3) == []  # out of range dropped, no crash


def _args(image, **kw):
    base = dict(
        image=str(image), profile=None, hint=None, pages="", out="", format="md"
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_non_image_non_pdf_fails_fast(tmp_path):
    f = tmp_path / "report.docx"
    f.write_bytes(b"PK\x03\x04")  # not a real docx, never read: rejected on suffix
    with pytest.raises(SystemExit) as ei:
        cli._run(_args(f), {})
    assert ei.value.code == 2


def test_pdf_runs_vision_per_page(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    pytest.importorskip("pypdfium2")

    # A 2-page PDF built from two blank images.
    p1 = Image.new("RGB", (160, 120), "white")
    p2 = Image.new("RGB", (160, 120), "gray")
    pdf = tmp_path / "diagrams.pdf"
    p1.save(str(pdf), save_all=True, append_images=[p2])

    # No real backend: pretend it is up and the pipeline returns a stub.
    # `available` is imported inside _run, so patch it at its source module.
    monkeypatch.setattr("cgh_vision.backends.available", lambda cfg: True)
    monkeypatch.setattr(cli, "_ensure_models", lambda cfg: None)
    seen = []

    def _fake_extract(image_path, config):
        seen.append(Path(image_path).suffix)
        return {
            "image": Path(image_path).name,
            "inventory": {"summary": "a diagram", "content": ["diagram"]},
            "diagram": None,
            "tables": [],
            "charts": [],
            "text": None,
        }

    monkeypatch.setattr(cli, "_extract_one", _fake_extract)

    out = tmp_path / "report.md"
    cli._run(_args(pdf, out=str(out)), {})

    body = out.read_text()
    assert "# diagrams.pdf" in body
    assert "## Page 1" in body and "## Page 2" in body
    assert seen == [".png", ".png"]  # each page rasterized to a png
