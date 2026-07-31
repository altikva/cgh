# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Qualitative pass of the vision benchmark on real images
#              dropped into bench/vision/real/ (screen photos of internal
#              diagrams, screenshots). No ground truth, so no scoring:
#              each model's raw JSON extraction is written to real_out/
#              for human review. real/ and real_out/ are gitignored on
#              purpose, real diagrams may contain confidential content
#              and must never land in the repo or leave the machine.

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from run_bench import MODELS, ask, parse_json

REAL = Path(__file__).parent / "real"
OUT = Path(__file__).parent / "real_out"


def main() -> None:
    images = sorted(
        p for p in REAL.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")
    )
    if not images:
        sys.exit("no real images in bench/vision/real/")
    OUT.mkdir(exist_ok=True)
    for model in sys.argv[1:] or MODELS:
        for img in images:
            t0 = time.time()
            try:
                raw, dt = ask(model, img)
            except Exception as exc:
                print(f"{model:20s} {img.stem:24s} ERROR {str(exc)[:60]}", flush=True)
                continue
            parsed = parse_json(raw)
            stem = f"{model.replace(':', '_').replace('/', '_')}__{img.stem}"
            (OUT / f"{stem}.raw.txt").write_text(raw, encoding="utf-8")
            if parsed is not None:
                (OUT / f"{stem}.json").write_text(
                    json.dumps(parsed, indent=2), encoding="utf-8"
                )
            nodes = len(parsed.get("nodes", [])) if parsed else 0
            edges = len(parsed.get("edges", [])) if parsed else 0
            print(
                f"{model:20s} {img.stem:24s} json={'y' if parsed else 'n'} "
                f"nodes={nodes:3d} edges={edges:3d} {dt:.0f}s",
                flush=True,
            )
            _ = t0


if __name__ == "__main__":
    main()
