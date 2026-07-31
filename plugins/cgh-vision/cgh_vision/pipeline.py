# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-31
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The image pipeline, ported from the vision benchmark that
#              shaped it. Pass 0 inventories the content with a
#              non-directive prompt (never assume a diagram); the router
#              runs only the extractors the content warrants: diagram
#              structure with the plain contract, enrichment over found
#              labels (cannot hurt recall, forbidden to invent a
#              legend), constrained edge reading by a second model,
#              table and chart extractors, dense-text summary.
#              Post-processing merges fuzzy-duplicate nodes, drops
#              arrow annotations mistaken for boxes, dedups reversed
#              edges, and splits identities (IPs, CIDRs, FQDNs, emails,
#              server names) out of labels into attributes for the
#              anonymization layer. Emitters produce markdown and
#              Mermaid (zones as subgraphs).

from __future__ import annotations

import json
import re
from pathlib import Path

from .backends import ask

# -- profiles ---------------------------------------------------------------
# Benchmark verdict: qwen2.5vl:3b owns nodes and zones (node P 1.00),
# gemma3:4b reads arrows once constrained to qwen's labels (edge recall
# 0.60 -> 0.80 for the ensemble).
PROFILES: dict[str, dict] = {
    "default": {
        "nodes_model": "qwen2.5vl:3b",
        "edges_model": "gemma3:4b",
        "read_legend": True,
        "read_title": True,
        "read_notes": True,
        "timeout_s": 120,
    },
    "fast": {
        "nodes_model": "qwen2.5vl:3b",
        "edges_model": None,
        "read_legend": False,
        "read_title": False,
        "read_notes": False,
        "timeout_s": 60,
    },
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


def profile_for(config: dict) -> dict:
    """Resolve the profile from [plugin.vision] config: `profile` picks
    a base, per-key overrides (nodes_model, edges_model, timeout_s,
    ollama_url) apply on top."""
    base = dict(
        PROFILES.get(str(config.get("profile", "default")), PROFILES["default"])
    )
    for key in ("nodes_model", "edges_model", "timeout_s"):
        if key in config:
            base[key] = config[key]
    return base


# -- prompts ----------------------------------------------------------------

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

STRUCTURE_PROMPT = """You are reading a technical architecture diagram.
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{"nodes": ["label", ...],
 "edges": [["source label", "target label"], ...],
 "zones": ["zone label", ...]}
Rules: copy node labels exactly as written in the image. An edge is a
drawn arrow between two boxes, directed from source to target. A zone
is a larger labeled rectangle grouping several boxes. If none, use [].
"""

PHOTO_HINT = (
    "\nThe image may be a photo of a screen: expect noise, moire and "
    "glare; transcribe labels as faithfully as possible and skip "
    "anything unreadable rather than guessing."
)

_ENRICH_SCHEMA = """{"title": "diagram title or empty string",
 "kinds": {"node label": "service|database|queue|storage|user|network|external|other", ...},
 "tech": {"node label": "product name recognizable from icon or text", ...}%s%s}"""
_ENRICH_LEGEND = """,
 "legend": [{"symbol": "...", "meaning": "..."}]"""
_ENRICH_NOTES = """,
 "notes": ["free-standing text annotation", ...]"""

EDGE_PROMPT = """You are reading a technical architecture diagram.
The boxes in this diagram are exactly these labels:
{nodes}
Return ONLY a JSON object, no prose, no markdown fence, with exactly:
{{"edges": [{{"source": "node label", "target": "node label", "label": "text on the arrow, else empty"}}]}}
listing every drawn arrow or line between two of these boxes, directed
from source to target, using exactly the labels above.
"""

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


def build_enrich_prompt(profile: dict, labels: list[str]) -> str:
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


# -- parsing helpers --------------------------------------------------------


def parse_json(text: str) -> dict | None:
    for candidate in (text, *re.findall(r"\{.*\}", text, re.DOTALL)):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def parse_edge_list(raw: str) -> list:
    """The edge pass sometimes returns a bare JSON array; accept both.
    The array scan runs first: parse_json would otherwise fish the
    first object out of the array (an edge, not an envelope) and hide
    every edge behind a missing "edges" key."""
    for candidate in re.findall(r"\[.*\]", raw, re.DOTALL):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, list):
                return obj
        except ValueError:
            continue
    parsed = parse_json(raw)
    if isinstance(parsed, dict):
        return list(parsed.get("edges") or [])
    return []


def norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(label).lower())


def _label(x) -> str:
    if isinstance(x, dict):
        for k in ("label", "name", "id", "text", "title"):
            if x.get(k):
                return str(x[k])
        return ""
    return str(x)


# -- identity separation ----------------------------------------------------

_IDENTITY = re.compile(
    r"(?P<cidr>\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b)"
    r"|(?P<ip>\b\d{1,3}(?:\.\d{1,3}){3}\b)"
    r"|(?P<email>\b[\w.+-]+@[\w-]+\.[\w.]+\b)"
    r"|(?P<fqdn>\b[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*){2,}\b)"
    r"|(?P<hostname>\b(?=\w*\d)[A-Za-z0-9][A-Za-z0-9-]{4,}\b(?:\.[a-z][\w.-]+)?)"
)


def split_identities(label: str) -> tuple[str, list[str]]:
    """Pull IPs, CIDRs, emails, FQDNs and server-ish hostnames out of a
    node label. The clean label keeps the human name; a label that was
    only an identity keeps it (anonymization placeholders it later)."""
    identities = [m.group(0) for m in _IDENTITY.finditer(label)]
    clean = _IDENTITY.sub("", label)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" -()[]/\n\t")
    return (clean or label.strip(), identities)


# -- post-processing --------------------------------------------------------


def postprocess(pred: dict) -> dict:
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
    by_pair: dict[frozenset, dict] = {}
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
        if pair in by_pair:
            # A labeled duplicate enriches the kept edge (the structure
            # pass emits bare pairs, the edge pass reads the arrow text).
            if lbl and not by_pair[pair]["label"]:
                by_pair[pair]["label"] = lbl
            continue
        edge = {"source": a, "target": b, "label": lbl}
        by_pair[pair] = edge
        edges.append(edge)

    zones = []
    for z in pred.get("zones") or []:
        label = _label(z)
        if not label.strip():
            continue
        raw_members = z.get("members") or [] if isinstance(z, dict) else []
        members = [r for m in raw_members if (r := _resolve(str(m)))]
        zones.append({"label": label, "members": members})

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


# -- passes -----------------------------------------------------------------


def inventory(path: Path, config: dict) -> dict:
    """Pass 0: what does the image contain?"""
    profile = profile_for(config)
    raw = ask(
        profile["nodes_model"],
        path,
        INVENTORY_PROMPT,
        config,
        int(profile.get("timeout_s", 120)),
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


def extract_diagram(path: Path, config: dict) -> dict:
    """Diagram extraction: structure, optional enrichment, constrained
    edges, post-processing. Returns the extraction dict plus rendered
    `markdown` and `mermaid` keys."""
    profile = profile_for(config)
    timeout = int(profile.get("timeout_s", 120))
    prompt = STRUCTURE_PROMPT + (PHOTO_HINT if profile.get("photo_hint") else "")
    raw = ask(profile["nodes_model"], path, prompt, config, timeout)
    pred = parse_json(raw) or {}
    labels = [_label(n) for n in (pred.get("nodes") or []) if _label(n).strip()]

    if labels and (
        profile.get("read_title")
        or profile.get("read_legend")
        or profile.get("read_notes")
    ):
        raw_meta = ask(
            profile["nodes_model"],
            path,
            build_enrich_prompt(profile, labels),
            config,
            timeout,
        )
        meta = parse_json(raw_meta) or {}
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
        raw_e = ask(
            profile["edges_model"],
            path,
            EDGE_PROMPT.format(nodes="\n".join(f"- {n}" for n in labels)),
            config,
            timeout,
        )
        pred["edges"] = list(pred.get("edges") or []) + parse_edge_list(raw_e)

    out = postprocess(pred)
    out["mermaid"] = to_mermaid(out)
    out["markdown"] = to_markdown(out)
    return out


def extract_tables(path: Path, config: dict) -> list[dict]:
    profile = profile_for(config)
    raw = ask(
        profile["nodes_model"], path, TABLE_PROMPT, config, int(profile["timeout_s"])
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


def extract_charts(path: Path, config: dict) -> list[dict]:
    profile = profile_for(config)
    raw = ask(
        profile["nodes_model"], path, CHART_PROMPT, config, int(profile["timeout_s"])
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


def extract_text(path: Path, config: dict) -> dict:
    profile = profile_for(config)
    raw = ask(
        profile["nodes_model"], path, TEXT_PROMPT, config, int(profile["timeout_s"])
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
            parts.append(f"- **{n['label']}**{tech}, {n['kind']}")
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
            parts.append(f"- {entry.get('symbol', '?')}: {entry.get('meaning', '')}")
        parts.append("")
    if ex["notes"]:
        parts.append("## Notes")
        parts.extend(f"- {t}" for t in ex["notes"])
        parts.append("")
    parts += ["## Diagram", "", "```mermaid", to_mermaid(ex), "```", ""]
    return "\n".join(parts)


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


# -- router -----------------------------------------------------------------

DIAGRAM_KINDS = {"architecture_diagram", "flowchart"}


def route(path: Path, config: dict) -> tuple[dict, str]:
    """The full pipeline: inventory, then only the extractors the
    content warrants. Returns (inventory, markdown)."""
    inv = inventory(path, config)
    parts = [f"# {path.name}", "", f"_{inv['summary']}_", ""]
    parts.append(f"Detected content: {', '.join(inv['content'])}")
    parts.append("")

    if DIAGRAM_KINDS & set(inv["content"]):
        ex = extract_diagram(path, config)
        parts.append(ex["markdown"])
    if "table" in inv["content"]:
        tables = extract_tables(path, config)
        if tables:
            parts.append("## Tables")
            parts.append(tables_to_markdown(tables))
    if "chart" in inv["content"]:
        charts = extract_charts(path, config)
        if charts:
            parts.append("## Charts")
            parts.append(charts_to_markdown(charts))
    if "dense_text" in inv["content"] or (
        inv["text_density"] == "dense"
        and not ({"table", *DIAGRAM_KINDS} & set(inv["content"]))
    ):
        txt = extract_text(path, config)
        if txt["summary"]:
            parts.append("## Text")
            if txt["title"]:
                parts.append(f"### {txt['title']}")
            parts.append(txt["summary"])
            parts.extend(f"- {k}" for k in txt["key_points"])
            parts.append("")
    return inv, "\n".join(parts)
