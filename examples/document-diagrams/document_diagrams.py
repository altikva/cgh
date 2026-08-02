# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-02
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Documents carry diagrams too: this example pulls the
#              embedded images out of a .pdf, .docx or .pptx, asks the
#              vision inventory what each one contains, and extracts
#              architecture schemas to markdown + Mermaid. Office files
#              are zip containers (stdlib only); PDF needs pypdf with
#              its image extra. Everything runs against a local Ollama
#              daemon.
#              Requires: pip install cgh cgh-vision
#                        pip install "pypdf[image]"   (PDF input only)
#              Usage:    python document_diagrams.py <file> [<file> ...]

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

from codegraph import sdk

DIAGRAM_KINDS = {"architecture_diagram", "flowchart", "network_diagram", "uml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MIN_BYTES = 5 * 1024  # skip icons and bullet glyphs


def images_from_office(path: Path, out_dir: Path) -> list[Path]:
    """.docx and .pptx are zips; embedded media sits under word/media/
    or ppt/media/. No dependency needed."""
    extracted: list[Path] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            p = Path(name)
            if "/media/" not in f"/{name}" or p.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            data = zf.read(name)
            if len(data) < MIN_BYTES:
                continue
            target = out_dir / f"{path.stem}__{p.name}"
            target.write_bytes(data)
            extracted.append(target)
    return extracted


def images_from_pdf(path: Path, out_dir: Path) -> list[Path]:
    """Embedded raster images of each PDF page, via pypdf."""
    try:
        import PIL  # noqa: F401  (pypdf image extraction hard-requires it)
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit(
            'PDF input needs pypdf with its image extra: pip install "pypdf[image]"'
        ) from None
    extracted: list[Path] = []
    reader = PdfReader(str(path))
    for page_no, page in enumerate(reader.pages, start=1):
        for img in page.images:
            suffix = Path(img.name).suffix.lower() or ".png"
            if suffix not in IMAGE_SUFFIXES or len(img.data) < MIN_BYTES:
                continue
            target = out_dir / f"{path.stem}__p{page_no}_{Path(img.name).name}"
            target.write_bytes(img.data)
            extracted.append(target)
    return extracted


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python document_diagrams.py <pdf|docx|pptx> ...")
    work = Path(tempfile.mkdtemp(prefix="cgh-doc-diagrams-"))
    for arg in sys.argv[1:]:
        doc = Path(arg)
        suffix = doc.suffix.lower()
        if suffix == ".pdf":
            images = images_from_pdf(doc, work)
        elif suffix in (".docx", ".pptx"):
            images = images_from_office(doc, work)
        else:
            print(f"{doc.name}: unsupported ({suffix}), skipping")
            continue
        print(f"{doc.name}: {len(images)} embedded image(s)")

        for img in images:
            inv = sdk.image_inventory(img)
            kinds = ",".join(inv["content"])
            print(f"  {img.name}: {kinds or 'nothing recognized'}")
            if not (DIAGRAM_KINDS & set(inv["content"])):
                continue
            ex = sdk.extract_diagram(img)
            report = doc.with_suffix("").parent / f"{img.stem}.md"
            report.write_text(ex["markdown"], encoding="utf-8")
            idents = [i for n in ex["nodes"] for i in n["identities"]]
            print(
                f"    -> {len(ex['nodes'])} nodes, {len(ex['edges'])} edges"
                + (f", {len(idents)} identity(ies) separated" if idents else "")
                + f", report: {report}"
            )


if __name__ == "__main__":
    main()
