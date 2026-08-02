# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Corpus insights: batch the gate-cleared summaries into one
#              model call and ask for what no single-file view shows,
#              hidden patterns, duplicated concepts, architectural drift.
#              Results are persisted to the knowledge store so agents
#              recall them in later sessions instead of re-deriving them.

from __future__ import annotations

from pathlib import Path

from .backends import pick_backend
from .gate import cloud_allowed

_MAX_PROMPT_CHARS = 24_000


def collect_summaries(repo_root: str | Path, config: dict) -> tuple[list[dict], int]:
    """Gate-cleared summary findings. Returns (rows, excluded_count):
    summaries of files the gate would not send stay out of cloud-bound
    batches, and the caller reports how many were withheld."""
    from codegraph.plugin_api import query_findings

    rows = [
        r
        for r in query_findings(repo_root, key_prefix="summary", limit=1000)
        if r["key"] == "summary"
    ]
    cleared: list[dict] = []
    excluded = 0
    for row in rows:
        allowed, _ = cloud_allowed(repo_root, row["file"], config)
        if allowed:
            cleared.append(row)
        else:
            excluded += 1
    return cleared, excluded


def build_insights_prompt(rows: list[dict], question: str, language: str) -> str:
    ask = question or (
        "What patterns, duplicated concepts, architectural drift or "
        "surprising couplings do these file summaries reveal that no "
        "single file shows? Be concrete, name files."
    )
    blocks: list[str] = []
    used = 0
    for row in rows:
        block = f"FILE: {row['file']}\nSUMMARY: {row['value']}\n"
        if used + len(block) > _MAX_PROMPT_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return (
        f"You are reviewing a codebase through its file summaries. "
        f"Answer in {language}, plain prose.\n{ask}\n\n" + "\n".join(blocks)
    )


def run_insights(
    repo_root: str | Path,
    config: dict,
    extras_fn=None,
    question: str = "",
) -> dict:
    """Run the corpus pass. Returns {"text", "files", "excluded",
    "backend", "knowledge_id"} or {"error": ...}."""
    rows, excluded = collect_summaries(repo_root, config)
    if not rows:
        return {
            "error": "no gate-cleared summaries yet, run cgh summarize run first",
            "excluded": excluded,
        }

    backend = pick_backend(
        config, extras=list((extras_fn or (lambda: []))()), cloud_allowed=True
    )
    if backend is None or backend.name == "structural":
        return {
            "error": "no model backend available (structural cannot analyze a corpus)",
            "excluded": excluded,
        }

    prompt = build_insights_prompt(rows, question, str(config.get("language", "en")))
    text = (backend.summarize(prompt, config) or "").strip()
    if not text:
        return {"error": f"backend {backend.name} returned nothing"}

    from codegraph.plugin_api import activity_log, knowledge_record

    try:
        activity_log(
            repo_root, "summarize", f"insights: {len(rows)} summaries to {backend.name}"
        )
    except Exception:
        pass
    knowledge_id = knowledge_record(
        title=f"Corpus insights ({len(rows)} files)",
        body=text,
        kind="pattern",
        tags="insights",
        repo_root=repo_root,
    )
    return {
        "text": text,
        "files": len(rows),
        "excluded": excluded,
        "backend": backend.name,
        "knowledge_id": knowledge_id,
    }
