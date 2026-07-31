# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The vision-model benchmark for proposal 006. Sends each
#              generated diagram to every candidate model through the
#              local Ollama daemon, asks for a strict JSON extraction
#              (nodes, directed edges, zones), then scores against the
#              ground truth: node precision/recall (fuzzy label match),
#              edge recall, zone recall, JSON compliance, PII echo (does
#              the model copy the bait emails/IPs, which anonymization
#              must later catch), and latency. Writes RESULTS.md.

from __future__ import annotations

import base64
import difflib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"
OLLAMA = "http://127.0.0.1:11434"

MODELS = sys.argv[1:] or [
    "moondream",
    "granite3.2-vision",
    "qwen2.5vl:3b",
    "gemma3:4b",
    "ensemble",
]

# The ensemble plays each model to its measured strength: qwen owns
# nodes and zones (best precision, best JSON discipline), then gemma
# reads the arrows constrained to qwen's node list, and the edge sets
# are unioned. Same total locality, roughly the sum of both latencies.
ENSEMBLE_NODES = "qwen2.5vl:3b"
ENSEMBLE_EDGES = "gemma3:4b"

EDGE_PROMPT = """You are reading a technical architecture diagram.
The boxes in this diagram are exactly these labels:
{nodes}
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{{"edges": [["source label", "target label"], ...]}}
listing every drawn arrow between two of these boxes, directed from
source to target, using exactly the labels above.
"""

PROMPT = """You are reading a technical architecture diagram.
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{"nodes": ["label", ...],
 "edges": [["source label", "target label"], ...],
 "zones": ["zone label", ...]}
Rules: copy node labels exactly as written in the image. An edge is a
drawn arrow between two boxes, directed from source to target. A zone
is a larger labeled rectangle grouping several boxes. If none, use [].
"""

PII_BAIT = re.compile(
    r"admin@acme-corp\.com|10\.128\.0\.5|ldap\.acme\.internal|prj-data-prod-001"
)


def ask(
    model: str, image_path: Path, prompt: str = PROMPT, timeout_s: int = 600
) -> tuple[str, float]:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "images": [base64.b64encode(image_path.read_bytes()).decode()],
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        out = json.loads(resp.read().decode())
    return out.get("response", ""), time.time() - t0


def ask_ensemble(image_path: Path) -> tuple[str, float, dict | None]:
    """Two-pass extraction: nodes/zones from ENSEMBLE_NODES, then
    ENSEMBLE_EDGES reads the arrows constrained to that node list; the
    two edge sets are unioned. Returns (raw concat, total secs, pred)."""
    raw_n, dt_n = ask(ENSEMBLE_NODES, image_path)
    base = parse_json(raw_n) or {}
    nodes = [_label(n) for n in (base.get("nodes") or []) if _label(n).strip()]
    if not nodes:
        return raw_n, dt_n, None
    prompt = EDGE_PROMPT.format(nodes="\n".join(f"- {n}" for n in nodes))
    raw_e, dt_e = ask(ENSEMBLE_EDGES, image_path, prompt=prompt)
    extra = parse_json(raw_e) or {}
    pred = {
        "nodes": nodes,
        "edges": list(base.get("edges") or []) + list(extra.get("edges") or []),
        "zones": base.get("zones") or [],
    }
    return raw_n + "\n" + raw_e, dt_n + dt_e, pred


def parse_json(text: str) -> dict | None:
    """Best-effort: strict parse, then the largest {...} block."""
    for candidate in (text, *re.findall(r"\{.*\}", text, re.DOTALL)):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(label).lower())


def match(pred: str, truths: list[str]) -> str | None:
    """Fuzzy label match: exact normalized, containment, then ratio."""
    p = norm(pred)
    if not p:
        return None
    for t in truths:
        if p == norm(t):
            return t
    for t in truths:
        nt = norm(t)
        if (p in nt or nt in p) and min(len(p), len(nt)) >= 4:
            return t
    best = max(truths, key=lambda t: difflib.SequenceMatcher(None, p, norm(t)).ratio())
    if difflib.SequenceMatcher(None, p, norm(best)).ratio() >= 0.75:
        return best
    return None


def _label(x) -> str:
    """Canonical label from whatever shape the model chose: a plain
    string, or an object keyed label/name/id/text/title."""
    if isinstance(x, dict):
        for k in ("label", "name", "id", "text", "title"):
            if x.get(k):
                return str(x[k])
        return ""
    return str(x)


def _edge_pair(e) -> tuple[str, str] | None:
    if isinstance(e, dict):
        for a_key, b_key in (("source", "target"), ("from", "to"), ("src", "dst")):
            if e.get(a_key) is not None and e.get(b_key) is not None:
                return _label(e[a_key]), _label(e[b_key])
        return None
    if isinstance(e, (list, tuple)) and len(e) == 2:
        return _label(e[0]), _label(e[1])
    return None


def score(pred: dict | None, truth: dict) -> dict:
    if pred is None:
        return {
            "json_ok": 0,
            "node_p": 0.0,
            "node_r": 0.0,
            "edge_p": 0.0,
            "edge_r": 0.0,
            "zone_r": 0.0,
        }
    pred_nodes = [_label(n) for n in (pred.get("nodes") or []) if _label(n).strip()]
    mapping: dict[str, str] = {}
    matched: set[str] = set()
    for pn in pred_nodes:
        m = match(pn, truth["nodes"])
        if m and m not in matched:
            mapping[norm(pn)] = m
            matched.add(m)
    node_p = len(matched) / len(pred_nodes) if pred_nodes else 0.0
    node_r = len(matched) / len(truth["nodes"])

    truth_edges = {(a, b) for a, b in truth["edges"]}
    pred_pairs = [p for e in (pred.get("edges") or []) if (p := _edge_pair(e))]
    matched_truth: set[frozenset] = set()
    good_preds = 0
    for a_raw, b_raw in pred_pairs:
        a, b = mapping.get(norm(a_raw)), mapping.get(norm(b_raw))
        # Direction is the hardest part for small VLMs; count either way
        # but track it, extraction without direction still builds a graph.
        if a and b and ((a, b) in truth_edges or (b, a) in truth_edges):
            good_preds += 1
            matched_truth.add(frozenset((a, b)))
    edge_r = len(matched_truth) / len(truth_edges) if truth_edges else 1.0
    edge_p = good_preds / len(pred_pairs) if pred_pairs else 0.0

    zones = truth.get("zones") or []
    zhit = (
        sum(1 for z in (pred.get("zones") or []) if match(_label(z), zones))
        if zones
        else 0
    )
    zone_r = zhit / len(zones) if zones else 1.0
    return {
        "json_ok": 1,
        "node_p": node_p,
        "node_r": node_r,
        "edge_p": edge_p,
        "edge_r": edge_r,
        "zone_r": zone_r,
    }


def main() -> None:
    images = sorted(DATA.glob("*.png"))
    if not images:
        sys.exit("no diagrams, run gen_diagrams.py first")
    rows: list[dict] = []
    for model in MODELS:
        for img in images:
            truth = json.loads(
                img.with_suffix("").with_suffix(".truth.json").read_text()
            )
            try:
                if model == "ensemble":
                    raw, dt, pred = ask_ensemble(img)
                else:
                    raw, dt = ask(model, img)
                    pred = parse_json(raw)
            except Exception as exc:
                rows.append({"model": model, "img": img.stem, "error": str(exc)[:80]})
                continue
            s = score(pred, truth)
            s.update(
                model=model,
                img=img.stem,
                secs=round(dt, 1),
                pii_echo=int(bool(PII_BAIT.search(raw))),
            )
            rows.append(s)
            print(
                f"{model:20s} {img.stem:16s} json={s['json_ok']} "
                f"nodes P={s['node_p']:.2f} R={s['node_r']:.2f} "
                f"edges P={s['edge_p']:.2f} R={s['edge_r']:.2f} "
                f"zones R={s['zone_r']:.2f} "
                f"{s['secs']}s pii={s['pii_echo']}",
                flush=True,
            )
    (Path(__file__).parent / "results.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    write_report(rows)


def write_report(rows: list[dict]) -> None:
    by_model: dict[str, list[dict]] = {}
    for r in rows:
        by_model.setdefault(r["model"], []).append(r)
    lines = [
        "# Vision model benchmark (proposal 006)",
        "",
        "Synthetic architecture diagrams with exact ground truth; JSON",
        "extraction scored on node precision/recall (fuzzy labels), edge",
        "recall (either direction), zone recall. `pii` counts diagrams",
        "where the raw answer echoed the planted emails/IPs/project ids",
        "(expected: extraction copies labels; anonymization must catch).",
        "",
        "| model | json ok | node P | node R | edge P | edge R | zone R | avg s/img | pii echo |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for model, rs in by_model.items():
        ok = [r for r in rs if "error" not in r]
        errs = len(rs) - len(ok)
        if not ok:
            lines.append(f"| {model} | errors: {errs} | | | | | | |")
            continue

        def avg(key: str, ok: list = ok) -> float:
            return sum(r[key] for r in ok) / len(ok)

        lines.append(
            f"| {model} | {sum(r['json_ok'] for r in ok)}/{len(ok)}"
            + (f" ({errs} err)" if errs else "")
            + f" | {avg('node_p'):.2f} | {avg('node_r'):.2f} | {avg('edge_p'):.2f}"
            f" | {avg('edge_r'):.2f}"
            f" | {avg('zone_r'):.2f} | {avg('secs'):.0f} | "
            f"{sum(r.get('pii_echo', 0) for r in ok)}/{len(ok)} |"
        )
    lines.append("")
    (Path(__file__).parent / "RESULTS.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print("\nwrote RESULTS.md")


if __name__ == "__main__":
    main()
