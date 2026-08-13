#!/usr/bin/env python3
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __author__ = "jndjama (Joy Ndjama)"
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Rasterize each page of a PDF to a PNG with pypdfium2, so you can run
# `cgh vision page-N.png` on the pages you care about. This is the manual
# fallback; `cgh vision file.pdf` (with the cgh-vision[pdf] extra) does it
# for you. Requires: pip install pypdfium2

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Rasterize PDF pages to PNGs")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("pages"))
    ap.add_argument(
        "--scale", type=float, default=2.0, help="72 dpi x scale (2.0 = 144 dpi)"
    )
    args = ap.parse_args()

    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("pip install pypdfium2")
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(args.pdf))
    try:
        for i in range(len(doc)):
            out = args.out_dir / f"page-{i + 1}.png"
            doc[i].render(scale=args.scale).to_pil().save(str(out))
            print(out)
    finally:
        doc.close()
    print(f"{len(doc)} page(s) -> {args.out_dir}/  (now: cgh vision {args.out_dir}/page-1.png)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
