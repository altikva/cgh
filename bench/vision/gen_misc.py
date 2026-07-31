# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Generates the NON-diagram images of the mixed corpus: a
#              data table, a bar chart, a dense text page, a logo, and a
#              mixed table+chart page. Each ships a truth JSON listing
#              the content types the inventory pass must detect, so the
#              triage benchmark measures both hits (table detected as
#              table) and the failure the pass exists to prevent: a
#              logo or a text page extracted as an architecture diagram.

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "data_misc"


def _font(size: int):
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


TABLE = {
    "columns": ["Service", "CPU", "RAM (GB)", "Cost/mo"],
    "rows": [
        ["api-gateway", "2.0", "4", "$58"],
        ["auth-svc", "0.5", "1", "$14"],
        ["order-svc", "1.0", "2", "$29"],
        ["postgres-main", "4.0", "16", "$210"],
        ["redis-cache", "1.0", "8", "$95"],
    ],
}

CHART = {
    "title": "Deployments per week",
    "labels": ["W27", "W28", "W29", "W30", "W31"],
    "values": [12, 18, 9, 24, 31],
}

TEXT = (
    "Migration plan for the payment platform. The current monolith "
    "handles checkout, refunds and reconciliation in a single Django "
    "application backed by one PostgreSQL instance. Phase one splits "
    "the refund workflow into its own service behind the existing API "
    "gateway, with a shared outbox table to keep event ordering. Phase "
    "two moves reconciliation to a nightly batch on the data platform, "
    "reading from a read replica to protect checkout latency. Phase "
    "three retires the legacy admin screens once the new back office "
    "reaches feature parity. Rollback for each phase is a config flag; "
    "no phase deletes data before the following release. The target "
    "is one phase per sprint with a review gate between phases, and "
    "error budgets agreed with the SRE team before the first cutover."
)


def gen_table() -> None:
    f_head, f_cell = _font(18), _font(16)
    cols, rows = TABLE["columns"], TABLE["rows"]
    cw, rh, m = 160, 44, 40
    img = Image.new(
        "RGB", (2 * m + cw * len(cols), 2 * m + rh * (len(rows) + 1)), "white"
    )
    d = ImageDraw.Draw(img)
    for j, col in enumerate(cols):
        x0, y0 = m + j * cw, m
        d.rectangle([x0, y0, x0 + cw, y0 + rh], fill="#dbe6f5", outline="#666")
        d.text((x0 + 8, y0 + 12), col, fill="#111", font=f_head)
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            x0, y0 = m + j * cw, m + (i + 1) * rh
            d.rectangle([x0, y0, x0 + cw, y0 + rh], outline="#666")
            d.text((x0 + 8, y0 + 12), cell, fill="#111", font=f_cell)
    img.save(OUT / "m1_table.png")
    _truth("m1_table", ["table"])


def gen_chart() -> None:
    f_title, f_lbl = _font(20), _font(14)
    img = Image.new("RGB", (640, 420), "white")
    d = ImageDraw.Draw(img)
    d.text((180, 20), CHART["title"], fill="#111", font=f_title)
    base_y, max_h, bw = 360, 260, 70
    vmax = max(CHART["values"])
    d.line([60, base_y, 600, base_y], fill="#333", width=2)
    d.line([60, base_y, 60, base_y - max_h - 20], fill="#333", width=2)
    for i, (lbl, v) in enumerate(zip(CHART["labels"], CHART["values"])):
        x0 = 90 + i * (bw + 30)
        h = int(max_h * v / vmax)
        d.rectangle(
            [x0, base_y - h, x0 + bw, base_y], fill="#4a7fc1", outline="#1c3f77"
        )
        d.text((x0 + 22, base_y + 8), lbl, fill="#111", font=f_lbl)
        d.text((x0 + 24, base_y - h - 20), str(v), fill="#111", font=f_lbl)
    img.save(OUT / "m2_chart.png")
    _truth("m2_chart", ["chart"])


def gen_text() -> None:
    f = _font(16)
    img = Image.new("RGB", (760, 560), "white")
    d = ImageDraw.Draw(img)
    words, lines, line = TEXT.split(), [], ""
    for w in words:
        if len(line) + len(w) > 70:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    for i, ln in enumerate(lines):
        d.text((50, 40 + i * 28), ln, fill="#222", font=f)
    img.save(OUT / "m3_text.png")
    _truth("m3_text", ["dense_text"])


def gen_logo() -> None:
    img = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([80, 60, 320, 300], fill="#1c3f77")
    d.ellipse([130, 110, 270, 250], fill="white")
    d.text((150, 320), "ACME Corp", fill="#1c3f77", font=_font(30))
    img.save(OUT / "m4_logo.png")
    _truth("m4_logo", ["logo"])


def gen_mixed() -> None:
    gen_chart()
    chart = Image.open(OUT / "m2_chart.png")
    f_head, f_cell = _font(15), _font(13)
    img = Image.new("RGB", (1100, 460), "white")
    img.paste(chart.resize((580, 380)), (20, 30))
    d = ImageDraw.Draw(img)
    cw, rh = 115, 36
    for j, col in enumerate(TABLE["columns"]):
        x0, y0 = 620 + j * cw, 60
        d.rectangle([x0, y0, x0 + cw, y0 + rh], fill="#dbe6f5", outline="#666")
        d.text((x0 + 6, y0 + 9), col, fill="#111", font=f_head)
    for i, row in enumerate(TABLE["rows"]):
        for j, cell in enumerate(row):
            x0, y0 = 620 + j * cw, 60 + (i + 1) * rh
            d.rectangle([x0, y0, x0 + cw, y0 + rh], outline="#666")
            d.text((x0 + 6, y0 + 9), cell, fill="#111", font=f_cell)
    img.save(OUT / "m5_mixed.png")
    _truth("m5_mixed", ["chart", "table"])


def _truth(name: str, content: list[str]) -> None:
    (OUT / f"{name}.truth.json").write_text(
        json.dumps({"content": content}, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    gen_table()
    gen_chart()
    gen_text()
    gen_logo()
    gen_mixed()
    print(f"generated 5 misc images in {OUT}")
