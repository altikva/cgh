# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The enriched extraction pipeline the cgh-vision plugin
#              will ship, exercised here against the bench corpus.
#              Profiles parameterize the run (models, richness, timeout);
#              the model is asked for a typed schema (nodes with kind and
#              technology, labeled edges, zones with members, legend,
#              title, free-text notes); post-processing cleans what the
#              raw bench outputs showed models get wrong: duplicate
#              nodes, edge labels mistaken for nodes, identities (IPs,
#              hostnames, emails, CIDRs) embedded in labels, which are
#              split into attributes so the anonymize stage has a clean
#              target. Emitters turn the extraction into markdown and
#              Mermaid. CLI: python extract.py <image> [profile].

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from run_bench import PROMPT, _label, ask, norm, parse_json

# -- profiles ---------------------------------------------------------------
# One knob the config exposes; everything else derives from it. The
# plugin will map [plugin.vision] keys onto these fields.
PROFILES: dict[str, dict] = {
    # Two-pass ensemble, full schema: the benchmark winner.
    "default": {
        "nodes_model": "qwen2.5vl:3b",
        "edges_model": "gemma3:4b",
        "read_legend": True,
        "read_title": True,
        "read_notes": True,
        "timeout_s": 120,
    },
    # Single model, single call, structure only: for large batches.
    "fast": {
        "nodes_model": "qwen2.5vl:3b",
        "edges_model": None,
        "read_legend": False,
        "read_title": False,
        "read_notes": False,
        "timeout_s": 60,
    },
    # Screen photos: same as default but the prompt warns about noise
    # and the post-processing is more aggressive on near-duplicates.
    "photo": {
        "nodes_model": "qwen2.5vl:3b",
        "edges_model": "gemma3:4b",
        "read_legend": False,
        "read_title": True,
        "read_notes": True,
        "timeout_s": 180,
        "photo_hint": True,
    },
}

_ENRICH_SCHEMA = """{"title": "diagram title or empty string",
 "kinds": {"node label": "service|database|queue|storage|user|network|external|other", ...},
 "tech": {"node label": "product name recognizable from icon or text", ...}%s%s}"""

_ENRICH_LEGEND = """,
 "legend": [{"symbol": "...", "meaning": "..."}]"""
_ENRICH_NOTES = """,
 "notes": ["free-standing text annotation", ...]"""


def build_enrich_prompt(profile: dict, labels: list[str]) -> str:
    """Second pass: classify the already-extracted labels and read the
    peripheral text. Never re-lists nodes, so it cannot hurt recall."""
    schema = _ENRICH_SCHEMA % (
        _ENRICH_LEGEND if profile.get("read_legend") else "",
        _ENRICH_NOTES if profile.get("read_notes") else "",
    )
    rules = (
        "Rules: kinds and tech only for the listed labels, omit a label when unsure."
    )
    if profile.get("read_legend"):
        rules += (
            " Report a legend ONLY if the image actually contains a drawn "
            "legend box or key; if there is none, use []. Never invent one."
        )
    if profile.get("read_notes"):
        rules += " notes are text annotations outside any box; [] if none."
    return (
        "You are reading a technical architecture diagram. The boxes are "
        "exactly these labels:\n"
        + "\n".join(f"- {n}" for n in labels)
        + "\nReturn ONLY a JSON object, no prose, no markdown fence, "
        "with exactly:\n" + schema + "\n" + rules
    )


def parse_edge_list(raw: str) -> list:
    """The edge pass sometimes comes back as a bare JSON array instead
    of the requested object; accept both."""
    parsed = parse_json(raw)
    if isinstance(parsed, dict):
        return list(parsed.get("edges") or [])
    for candidate in re.findall(r"\[.*\]", raw, re.DOTALL):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, list):
                return obj
        except ValueError:
            continue
    return []


EDGE_PROMPT = """You are reading a technical architecture diagram.
The boxes in this diagram are exactly these labels:
{nodes}
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{{"edges": [{{"source": "node label", "target": "node label", "label": "text on the arrow, else empty"}}]}}
listing every drawn arrow or line between two of these boxes, directed
from source to target, using exactly the labels above.
"""

# -- identity separation ----------------------------------------------------
# What the anonymize stage will scrub; here it is split out of labels so
# the graph keys on stable names and the identities are attributes.
_IDENTITY = re.compile(
    r"(?P<cidr>\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b)"
    r"|(?P<ip>\b\d{1,3}(?:\.\d{1,3}){3}\b)"
    r"|(?P<email>\b[\w.+-]+@[\w-]+\.[\w.]+\b)"
    r"|(?P<fqdn>\b[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*){2,}\b)"
    r"|(?P<hostname>\b(?=\w*\d)[A-Za-z0-9][A-Za-z0-9-]{4,}\b(?:\.[a-z][\w.-]+)?)"
)


def split_identities(label: str) -> tuple[str, list[str]]:
    """Pull IPs, CIDRs, emails and server-ish hostnames out of a node
    label. Returns (clean label, identities). The clean label keeps the
    human name; a label that was only an identity keeps it (a node must
    keep some name, anonymization will placeholder it later)."""
    identities = [m.group(0) for m in _IDENTITY.finditer(label)]
    clean = _IDENTITY.sub("", label)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" -()[]/\n\t")
    return (clean or label.strip(), identities)


# -- post-processing --------------------------------------------------------


def postprocess(pred: dict) -> dict:
    """Clean a raw model extraction using what the bench outputs showed:
    fuzzy-duplicate nodes, edge labels sitting in the node list, node
    labels carrying identities, reversed duplicate edges."""
    nodes_in = pred.get("nodes") or []
    edges_in = pred.get("edges") or []

    edge_labels = {
        norm(str(e.get("label", "")))
        for e in edges_in
        if isinstance(e, dict) and e.get("label")
    }

    nodes: list[dict] = []
    seen: dict[str, dict] = {}
    for n in nodes_in:
        label = _label(n)
        if not label.strip():
            continue
        clean, identities = split_identities(label)
        key = norm(clean)
        if not key:
            continue
        # An entry that exactly matches an edge label and never appears
        # as an edge endpoint is an arrow annotation, not a box.
        if key in edge_labels and not _is_endpoint(label, edges_in):
            continue
        if key in seen:
            seen[key]["identities"] = sorted(
                set(seen[key]["identities"]) | set(identities)
            )
            continue
        node = {
            "label": clean,
            "kind": str(n.get("kind", "other")) if isinstance(n, dict) else "other",
            "tech": str(n.get("tech", "")) if isinstance(n, dict) else "",
            "identities": identities,
        }
        seen[key] = node
        nodes.append(node)

    def _resolve(raw: str) -> str | None:
        k = norm(split_identities(str(raw))[0])
        if k in seen:
            return seen[k]["label"]
        for key, node in seen.items():
            if k and (k in key or key in k) and min(len(k), len(key)) >= 4:
                return node["label"]
        return None

    edges: list[dict] = []
    seen_pairs: set[frozenset] = set()
    for e in edges_in:
        if isinstance(e, dict):
            src, dst, lbl = e.get("source"), e.get("target"), str(e.get("label", ""))
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            src, dst, lbl = e[0], e[1], ""
        else:
            continue
        a, b = _resolve(str(src)), _resolve(str(dst))
        if not a or not b or a == b:
            continue
        pair = frozenset((a, b))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        edges.append({"source": a, "target": b, "label": lbl})

    zones = []
    for z in pred.get("zones") or []:
        label = _label(z)
        if not label.strip():
            continue
        raw_members = z.get("members") or [] if isinstance(z, dict) else []
        members = [r for m in raw_members if (r := _resolve(str(m)))]
        zones.append({"label": label, "members": members})

    # Legend entries that echo the schema placeholders are inventions.
    _placeholder = {"", "...", "[]", "free-standing text annotation"}
    legend = [
        entry
        for entry in (pred.get("legend") or [])
        if isinstance(entry, dict)
        and str(entry.get("symbol", "")).strip() not in _placeholder
        and str(entry.get("meaning", "")).strip() not in _placeholder
    ]

    return {
        "title": str(pred.get("title", "")),
        "nodes": nodes,
        "edges": edges,
        "zones": zones,
        "legend": legend,
        "notes": [
            str(t)
            for t in (pred.get("notes") or [])
            if str(t).strip() and str(t).strip() not in _placeholder
        ],
    }


def _is_endpoint(label: str, edges: list) -> bool:
    k = norm(label)
    for e in edges:
        if isinstance(e, dict):
            if (
                norm(str(e.get("source", ""))) == k
                or norm(str(e.get("target", ""))) == k
            ):
                return True
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            if norm(str(e[0])) == k or norm(str(e[1])) == k:
                return True
    return False


# -- pipeline ---------------------------------------------------------------


def extract(image_path: Path, profile_name: str = "default") -> dict:
    """Run the profile's pipeline on one image and return the cleaned
    extraction. Three passes, each doing the one thing the benchmark
    showed it does well: structure with the plain contract (best node
    recall), optional enrichment over the found labels (cannot hurt
    recall, forbidden to invent a legend), then the constrained edge
    reading."""
    profile = PROFILES[profile_name]
    timeout = int(profile.get("timeout_s", 120))
    base_prompt = PROMPT + (
        "\nThe image may be a photo of a screen: expect noise, moire and "
        "glare; transcribe labels as faithfully as possible and skip "
        "anything unreadable rather than guessing."
        if profile.get("photo_hint")
        else ""
    )
    raw, _dt = ask(
        profile["nodes_model"], image_path, prompt=base_prompt, timeout_s=timeout
    )
    pred = parse_json(raw) or {}
    labels = [_label(n) for n in (pred.get("nodes") or []) if _label(n).strip()]

    if labels and (
        profile.get("read_title")
        or profile.get("read_legend")
        or profile.get("read_notes")
    ):
        raw_meta, _ = ask(
            profile["nodes_model"],
            image_path,
            prompt=build_enrich_prompt(profile, labels),
            timeout_s=timeout,
        )
        meta = parse_json(raw_meta) or {}
        # The model rarely reuses the exact label strings as keys;
        # join on normalized labels instead.
        kinds = {norm(k): str(v) for k, v in (meta.get("kinds") or {}).items()}
        techs = {norm(k): str(v) for k, v in (meta.get("tech") or {}).items()}
        pred["nodes"] = [
            {
                "label": n,
                "kind": kinds.get(norm(n), "other"),
                "tech": techs.get(norm(n), ""),
            }
            for n in labels
        ]
        pred.setdefault("title", meta.get("title", ""))
        pred["legend"] = meta.get("legend") or []
        pred["notes"] = meta.get("notes") or []

    if profile.get("edges_model") and labels:
        prompt = EDGE_PROMPT.format(nodes="\n".join(f"- {n}" for n in labels))
        raw_e, _ = ask(
            profile["edges_model"], image_path, prompt=prompt, timeout_s=timeout
        )
        pred["edges"] = list(pred.get("edges") or []) + parse_edge_list(raw_e)

    return postprocess(pred)


# -- emitters ---------------------------------------------------------------


def _mermaid_id(label: str, taken: dict[str, str]) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "_", label).strip("_") or "n"
    candidate, i = base, 1
    while candidate in taken and taken[candidate] != label:
        i += 1
        candidate = f"{base}_{i}"
    taken[candidate] = label
    return candidate


def to_mermaid(ex: dict) -> str:
    ids: dict[str, str] = {}
    by_label: dict[str, str] = {}
    lines = ["flowchart LR"]
    zoned = {m for z in ex["zones"] for m in z["members"] if m}
    for z in ex["zones"]:
        zid = _mermaid_id(z["label"], ids)
        lines.append(f'  subgraph {zid}["{z["label"]}"]')
        for m in z["members"]:
            if m:
                mid = by_label.setdefault(m, _mermaid_id(m, ids))
                lines.append(f'    {mid}["{m}"]')
        lines.append("  end")
    for n in ex["nodes"]:
        if n["label"] in zoned:
            continue
        nid = by_label.setdefault(n["label"], _mermaid_id(n["label"], ids))
        shape = (
            f'[("{n["label"]}")]'
            if n["kind"] == "database"
            else f'(["{n["label"]}"])'
            if n["kind"] == "user"
            else f'["{n["label"]}"]'
        )
        lines.append(f"  {nid}{shape}")
    for e in ex["edges"]:
        a = by_label.setdefault(e["source"], _mermaid_id(e["source"], ids))
        b = by_label.setdefault(e["target"], _mermaid_id(e["target"], ids))
        arrow = f'-- "{e["label"]}" -->' if e["label"] else "-->"
        lines.append(f"  {a} {arrow} {b}")
    return "\n".join(lines)


def to_markdown(ex: dict) -> str:
    parts = [f"# {ex['title'] or 'Architecture extraction'}", ""]
    if ex["nodes"]:
        parts.append("## Components")
        for n in ex["nodes"]:
            tech = f" ({n['tech']})" if n["tech"] else ""
            ids = (
                f"  <!-- identities: {', '.join(n['identities'])} -->"
                if n["identities"]
                else ""
            )
            parts.append(f"- **{n['label']}**{tech}, {n['kind']}{ids}")
        parts.append("")
    if ex["zones"]:
        parts.append("## Zones")
        for z in ex["zones"]:
            members = ", ".join(m for m in z["members"] if m)
            parts.append(f"- **{z['label']}**: {members}")
        parts.append("")
    if ex["legend"]:
        parts.append("## Legend")
        for entry in ex["legend"]:
            if isinstance(entry, dict):
                parts.append(
                    f"- {entry.get('symbol', '?')}: {entry.get('meaning', '')}"
                )
        parts.append("")
    if ex["notes"]:
        parts.append("## Notes")
        parts.extend(f"- {t}" for t in ex["notes"])
        parts.append("")
    parts += ["## Diagram", "", "```mermaid", to_mermaid(ex), "```", ""]
    return "\n".join(parts)


# -- pass 0: content inventory ----------------------------------------------
# Non-directive on purpose: the extraction prompts presuppose a diagram,
# which makes small VLMs invent one when shown a logo or a text page.
# The inventory asks what the image contains and the router decides
# which extractors (if any) are worth running.

CONTENT_TYPES = (
    "architecture_diagram",
    "flowchart",
    "chart",
    "table",
    "dense_text",
    "code",
    "ui_screenshot",
    "logo",
    "photo",
    "handwriting",
    "other",
)

INVENTORY_PROMPT = (
    "Look at this image and inventory what it contains. Do not assume "
    "it is a technical image.\n"
    "Return ONLY a JSON object, no prose, no markdown fence, with exactly:\n"
    '{"summary": "one sentence describing what the image shows",\n'
    ' "content": ["' + '" | "'.join(CONTENT_TYPES) + '", ...],\n'
    ' "text_density": "none" | "sparse" | "dense"}\n'
    "List every content type present; an image can contain several. "
    'Use ["other"] when nothing fits.'
)

TABLE_PROMPT = """This image contains one or more data tables.
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{"tables": [{"title": "table title or empty string",
             "columns": ["header", ...],
             "rows": [["cell", ...], ...]}]}
Copy cell text exactly as written. One entry per table.
"""

CHART_PROMPT = """This image contains one or more charts.
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{"charts": [{"type": "bar|line|pie|scatter|area|other",
             "title": "chart title or empty string",
             "x_axis": "x axis label or empty",
             "y_axis": "y axis label or empty",
             "values": [["category or x", "value"], ...],
             "insight": "one sentence stating what the chart shows"}]}
Read data points from the chart as precisely as the image allows.
"""

TEXT_PROMPT = """This image is mostly text.
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{"title": "document title or empty string",
 "summary": "3 to 5 sentences summarizing the text",
 "key_points": ["short bullet", ...]}
Base everything strictly on the text visible in the image.
"""


def inventory(image_path: Path, profile_name: str = "default") -> dict:
    profile = PROFILES[profile_name]
    timeout = int(profile.get("timeout_s", 120))
    raw, _ = ask(
        profile["nodes_model"], image_path, prompt=INVENTORY_PROMPT, timeout_s=timeout
    )
    parsed = parse_json(raw) or {}
    content = [
        str(c) for c in (parsed.get("content") or []) if str(c) in CONTENT_TYPES
    ] or ["other"]
    return {
        "summary": str(parsed.get("summary", "")),
        "content": content,
        "text_density": str(parsed.get("text_density", "none")),
    }


def extract_tables(image_path: Path, profile: dict) -> list[dict]:
    raw, _ = ask(
        profile["nodes_model"],
        image_path,
        prompt=TABLE_PROMPT,
        timeout_s=int(profile.get("timeout_s", 120)),
    )
    parsed = parse_json(raw) or {}
    out = []
    for tb in parsed.get("tables") or []:
        if not isinstance(tb, dict):
            continue
        cols = [str(c) for c in (tb.get("columns") or [])]
        rows = [
            [str(c) for c in r] for r in (tb.get("rows") or []) if isinstance(r, list)
        ]
        if cols and rows:
            out.append(
                {"title": str(tb.get("title", "")), "columns": cols, "rows": rows}
            )
    return out


def extract_charts(image_path: Path, profile: dict) -> list[dict]:
    raw, _ = ask(
        profile["nodes_model"],
        image_path,
        prompt=CHART_PROMPT,
        timeout_s=int(profile.get("timeout_s", 120)),
    )
    parsed = parse_json(raw) or {}
    out = []
    for ch in parsed.get("charts") or []:
        if not isinstance(ch, dict):
            continue
        values = [
            [str(a), str(b)]
            for item in (ch.get("values") or [])
            if isinstance(item, (list, tuple)) and len(item) == 2
            for a, b in [item]
        ]
        out.append(
            {
                "type": str(ch.get("type", "other")),
                "title": str(ch.get("title", "")),
                "x_axis": str(ch.get("x_axis", "")),
                "y_axis": str(ch.get("y_axis", "")),
                "values": values,
                "insight": str(ch.get("insight", "")),
            }
        )
    return out


def extract_text(image_path: Path, profile: dict) -> dict:
    raw, _ = ask(
        profile["nodes_model"],
        image_path,
        prompt=TEXT_PROMPT,
        timeout_s=int(profile.get("timeout_s", 120)),
    )
    parsed = parse_json(raw) or {}
    return {
        "title": str(parsed.get("title", "")),
        "summary": str(parsed.get("summary", "")),
        "key_points": [
            str(k).lstrip("-* ").strip()
            for k in (parsed.get("key_points") or [])
            if str(k).strip()
        ],
    }


def tables_to_markdown(tables: list[dict]) -> str:
    parts = []
    for tb in tables:
        if tb["title"]:
            parts.append(f"### {tb['title']}")
        parts.append("| " + " | ".join(tb["columns"]) + " |")
        parts.append("|" + "---|" * len(tb["columns"]))
        for row in tb["rows"]:
            parts.append("| " + " | ".join(row) + " |")
        parts.append("")
    return "\n".join(parts)


def charts_to_markdown(charts: list[dict]) -> str:
    parts = []
    for ch in charts:
        title = ch["title"] or f"{ch['type']} chart"
        parts.append(f"### {title}")
        if ch["insight"]:
            parts.append(f"_{ch['insight']}_")
        axis = " / ".join(x for x in (ch["x_axis"], ch["y_axis"]) if x)
        if axis:
            parts.append(f"Axes: {axis}")
        if ch["values"]:
            parts.append("")
            parts.append(
                "| " + (ch["x_axis"] or "x") + " | " + (ch["y_axis"] or "value") + " |"
            )
            parts.append("|---|---|")
            for a, b in ch["values"]:
                parts.append(f"| {a} | {b} |")
        parts.append("")
    return "\n".join(parts)


def route(image_path: Path, profile_name: str = "default") -> str:
    """The full pipeline: inventory first, then only the extractors the
    content warrants. Returns markdown; an image with nothing technical
    costs exactly one model call and one summary line."""
    profile = PROFILES[profile_name]
    inv = inventory(image_path, profile_name)
    parts = [f"# {image_path.name}", "", f"_{inv['summary']}_", ""]
    parts.append(f"Detected content: {', '.join(inv['content'])}")
    parts.append("")

    if {"architecture_diagram", "flowchart"} & set(inv["content"]):
        ex = extract(image_path, profile_name)
        parts.append(to_markdown(ex))
    if "table" in inv["content"]:
        tables = extract_tables(image_path, profile)
        if tables:
            parts.append("## Tables")
            parts.append(tables_to_markdown(tables))
    if "chart" in inv["content"]:
        charts = extract_charts(image_path, profile)
        if charts:
            parts.append("## Charts")
            parts.append(charts_to_markdown(charts))
    if "dense_text" in inv["content"] or (
        inv["text_density"] == "dense"
        and not ({"architecture_diagram", "flowchart", "table"} & set(inv["content"]))
    ):
        txt = extract_text(image_path, profile)
        if txt["summary"]:
            parts.append("## Text")
            if txt["title"]:
                parts.append(f"### {txt['title']}")
            parts.append(txt["summary"])
            parts.extend(f"- {k}" for k in txt["key_points"])
            parts.append("")
    return "\n".join(parts)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: extract.py <image> [profile]")
    image = Path(sys.argv[1])
    profile = sys.argv[2] if len(sys.argv) > 2 else "default"
    print(route(image, profile))
