# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Scores the pass-0 content inventory over the mixed corpus:
#              the synthetic architecture diagrams (truth: diagram) plus
#              the generated non-diagram images (table, chart, dense
#              text, logo, mixed). Reports per-image detected vs
#              expected, recall per truth type, and the metric the pass
#              exists for: false diagrams, images wrongly inventoried as
#              architecture diagrams that the router would then extract.

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from extract import inventory

HERE = Path(__file__).parent
# Diagram family counted as one: flowchart vs architecture_diagram is a
# judgment call the router treats identically.
DIAGRAM_KINDS = {"architecture_diagram", "flowchart"}


def _cases() -> list[tuple[Path, set[str]]]:
    cases = []
    for img in sorted((HERE / "data").glob("*.png")):
        cases.append((img, {"architecture_diagram"}))
    for img in sorted((HERE / "data_misc").glob("*.png")):
        truth = json.loads(img.with_suffix("").with_suffix(".truth.json").read_text())
        cases.append((img, set(truth["content"])))
    return cases


def _hit(expected: str, detected: set[str]) -> bool:
    if expected in DIAGRAM_KINDS:
        return bool(DIAGRAM_KINDS & detected)
    return expected in detected


def main() -> None:
    profile = sys.argv[1] if len(sys.argv) > 1 else "default"
    cases = _cases()
    if not cases:
        sys.exit("run gen_diagrams.py and gen_misc.py first")

    per_type: dict[str, list[int]] = {}
    false_diagrams = 0
    rows = []
    for img, expected in cases:
        t0 = time.time()
        inv = inventory(img, profile)
        detected = set(inv["content"])
        for exp in expected:
            per_type.setdefault(exp, []).append(int(_hit(exp, detected)))
        is_false_diagram = bool(DIAGRAM_KINDS & detected) and not (
            DIAGRAM_KINDS & expected
        )
        false_diagrams += is_false_diagram
        rows.append((img.stem, sorted(expected), sorted(detected), is_false_diagram))
        print(
            f"{img.stem:18s} expected={','.join(sorted(expected)):24s} "
            f"detected={','.join(sorted(detected)):40s} "
            f"{'FALSE-DIAGRAM ' if is_false_diagram else ''}{time.time() - t0:.0f}s",
            flush=True,
        )

    lines = [
        "",
        "## Inventory pass (mixed corpus)",
        "",
        f"{len(cases)} images: the 5 synthetic diagrams plus 5 generated",
        "non-diagram images (table, chart, dense text, logo, chart+table).",
        "",
        "| truth type | recall |",
        "|---|---|",
    ]
    for kind in sorted(per_type):
        hits = per_type[kind]
        lines.append(f"| {kind} | {sum(hits)}/{len(hits)} |")
    lines += [
        "",
        f"**False diagrams** (non-diagram inventoried as one, the failure "
        f"this pass exists to prevent): {false_diagrams}/{sum(1 for _, e in cases if not (DIAGRAM_KINDS & e))}",
        "",
    ]
    report = "\n".join(lines)
    with (HERE / "RESULTS.md").open("a", encoding="utf-8") as fh:
        fh.write(report)
    print(report)


if __name__ == "__main__":
    main()
