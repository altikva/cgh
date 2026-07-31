# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Builds a compact, ranked context block from graph + FTS
#              for a natural-language task description. Saves 60-90% of
#              exploration tokens by pre-computing the relevant subgraph.

from __future__ import annotations

from codegraph.core.utils import quiet_subprocess_kwargs

import sqlite3
from dataclasses import dataclass, field

from codegraph.core.fts import fts_search


@dataclass
class ContextNode:
    kind: str  # "function", "class", "md_section", "tf_resource"
    name: str
    file_path: str
    start_line: int
    end_line: int
    docstring: str
    relevance: float  # 0.0 - 1.0
    relationships: list[str] = field(default_factory=list)


@dataclass
class MemoryHit:
    key: str
    source: str  # "ruflo_memory" or "ruflo_pattern"
    content: str
    similarity: float


@dataclass
class MemoryDocHit:
    """A hit from the local Claude Code memory index (not Ruflo)."""

    path: str
    kind: str
    title: str
    snippet: str
    score: float


@dataclass
class PlanDocHit:
    """A hit from the local Claude Code plans index."""

    path: str
    slug: str
    agent_id: str
    title: str
    snippet: str
    score: float


@dataclass
class KnowledgeHit:
    """An entry from codegraph's knowledge store (patterns, decisions, gotchas…)."""

    id: int
    kind: str
    title: str
    body: str
    tags: list[str]
    score: float


@dataclass
class TaskContext:
    task: str
    nodes: list[ContextNode]
    files_referenced: list[str]
    token_estimate: int
    memory_hits: list[MemoryHit] = field(default_factory=list)
    memory_docs: list[MemoryDocHit] = field(default_factory=list)
    plan_docs: list[PlanDocHit] = field(default_factory=list)
    knowledge_docs: list[KnowledgeHit] = field(default_factory=list)


def _check_ruflo_available() -> bool:
    """Check if Ruflo (npx ruflo) is available. Cached after first check."""
    if not hasattr(_check_ruflo_available, "_cached"):
        import shutil
        import subprocess

        _check_ruflo_available._cached = False
        if shutil.which("npx"):
            try:
                r = subprocess.run(
                    ["npx", "ruflo", "--version"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                    **quiet_subprocess_kwargs(),
                )
                _check_ruflo_available._cached = r.returncode == 0
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                pass
    return _check_ruflo_available._cached


def _query_ruflo_memory(task: str, limit: int = 5) -> list[MemoryHit]:
    """
    Query Ruflo memory + pattern stores via MCP subprocess.
    Returns merged results from both knowledge memory and review patterns.
    Returns empty list if Ruflo is not installed, codegraph works standalone.
    """
    if not _check_ruflo_available():
        return []

    import json as _json
    import subprocess

    hits: list[MemoryHit] = []

    for source, cmd in [
        (
            "ruflo_memory",
            [
                "npx",
                "ruflo",
                "memory",
                "search",
                "--query",
                task,
                "--namespace",
                "ondonne",
                "--limit",
                str(limit),
            ],
        ),
        (
            "ruflo_pattern",
            [
                "npx",
                "ruflo",
                "hooks",
                "intelligence",
                "pattern-search",
                "--query",
                task,
                "--topK",
                str(limit),
            ],
        ),
    ]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                cwd=str(__import__("pathlib").Path.cwd()),
                **quiet_subprocess_kwargs(),
            )
            if result.returncode == 0 and result.stdout.strip():
                data = _json.loads(result.stdout)
                results = data.get("results", [])
                for r in results:
                    content = r.get("value", r.get("pattern", ""))
                    if isinstance(content, dict):
                        content = _json.dumps(content)
                    elif isinstance(content, str) and len(content) > 300:
                        content = content[:300]
                    sim = r.get("similarity", 0.5)
                    key = r.get("key", r.get("patternId", "?"))
                    hits.append(
                        MemoryHit(
                            key=key,
                            source=source,
                            content=content,
                            similarity=sim,
                        )
                    )
        except (
            subprocess.TimeoutExpired,
            FileNotFoundError,
            _json.JSONDecodeError,
            OSError,
        ):
            pass

    hits.sort(key=lambda h: h.similarity, reverse=True)
    return hits[:limit]


_STOPWORDS = frozenset(
    """
    a an the and or but if then else while when where why how what which who whom
    is are was were be been being have has had do does did doing can could should
    would will shall may might must of in on at by for with to from into onto off
    over under above below up down out about as per via vs versus through across
    this that these those it its they them their there here also more most less
    some any each all every few many much no not such than too very not so only
    just both not only either neither etc eg ie vs me my our us you your he she
    we they them il je nous vous ils elles tu se son sa ses leur leurs aux les
    des du la le un une de d l s t c n que qui dont
    """.split()
)


def _keyword_query(task: str, min_len: int = 3) -> str:
    """
    Turn a natural-language task description into an FTS5 OR query.

    Drops stopwords + very short tokens. Splits camelCase/snake_case so a
    task like "CerfaHandler refactor" yields tokens matching either
    `Cerfa`, `Handler`, or `refactor`.
    """
    import re

    # \w matches Unicode letters by default in Python, captures accented chars
    tokens = re.findall(r"[^\W\d_]+", task, flags=re.UNICODE)
    expanded: list[str] = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        # Split camelCase
        parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", t).split()
        for p in parts:
            # Split snake_case
            for q in p.split("_"):
                if len(q) >= min_len and q.lower() not in _STOPWORDS:
                    expanded.append(q)

    if not expanded:
        return task  # last-resort fallback

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for w in expanded:
        lw = w.lower()
        if lw not in seen:
            seen.add(lw)
            unique.append(w)

    # FTS5 OR syntax
    return " OR ".join(unique[:20])  # cap query length


def context_for_task(
    task: str,
    kuzu_conn,
    fts_conn: sqlite3.Connection,
    max_nodes: int = 15,
) -> TaskContext:
    """
    Build a ranked context for a natural-language task.
    1. Keyword-extract the task, FTS-search to find initial seed symbols
    2. Expand via graph edges (callers, callees, inheritance)
    3. Query Ruflo memory for project knowledge + review patterns
    4. Rank by relevance and return top-N
    """
    # Step 0: Query Ruflo memory (best-effort, non-blocking)
    memory_hits = _query_ruflo_memory(task)

    # Step 0b: Query local Claude Code memory + plans (SQLite FTS, always on)
    memory_docs = _local_memory_hits(fts_conn, task, limit=3)
    plan_docs = _local_plan_hits(fts_conn, task, limit=2)
    knowledge_docs = _local_knowledge_hits(task, limit=3)

    # Step 1: FTS search to find seed symbols. Natural-language sentences
    # don't match symbol names directly, extract keywords first.
    query = _keyword_query(task)
    fts_results = fts_search(fts_conn, query, limit=max_nodes * 2)
    if not fts_results and query != task:
        # Safety net: retry with the raw task if keyword query was too narrow
        fts_results = fts_search(fts_conn, task, limit=max_nodes * 2)

    # Step 2: Build context nodes from FTS results
    nodes: list[ContextNode] = []
    seen_ids: set[str] = set()

    for i, r in enumerate(fts_results):
        node_id = f"{r.file_path}:{r.start_line}"
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)

        # Normalize score to 0-1 range
        relevance = r.score / (fts_results[0].score if fts_results[0].score > 0 else 1)

        node = ContextNode(
            kind=r.kind,
            name=r.name,
            file_path=r.file_path,
            start_line=r.start_line,
            end_line=r.end_line,
            docstring=r.docstring,
            relevance=min(relevance, 1.0),
        )

        # Step 3: Expand via graph, find callers/callees (up to 3 each)
        if r.kind == "function":
            callers = kuzu_conn.find_neighbors(
                "CALLS",
                dst_where={"name": r.name},
                return_src=["name"],
            )[:3]
            for c in callers:
                node.relationships.append(f"called by {c['src_name']}")

            callees = kuzu_conn.find_neighbors(
                "CALLS",
                src_where={"name": r.name},
                return_dst=["name"],
            )[:3]
            for c in callees:
                node.relationships.append(f"calls {c['dst_name']}")

        elif r.kind == "class":
            parents = kuzu_conn.find_neighbors(
                "INHERITS",
                src_where={"name": r.name},
                return_dst=["name"],
            )[:3]
            for p in parents:
                node.relationships.append(f"extends {p['dst_name']}")

        nodes.append(node)

    # Sort by relevance, take top N
    nodes.sort(key=lambda n: n.relevance, reverse=True)
    nodes = nodes[:max_nodes]

    # Collect referenced files
    files = sorted(set(n.file_path for n in nodes))

    # Estimate tokens (rough: ~4 chars per token)
    total_chars = sum(
        len(n.name) + len(n.docstring) + len(n.file_path) + 50 for n in nodes
    )
    token_estimate = total_chars // 4

    return TaskContext(
        task=task,
        nodes=nodes,
        files_referenced=files,
        token_estimate=token_estimate,
        memory_hits=memory_hits,
        memory_docs=memory_docs,
        plan_docs=plan_docs,
        knowledge_docs=knowledge_docs,
    )


def _local_knowledge_hits(task: str, limit: int = 3) -> list[KnowledgeHit]:
    """Pull top knowledge entries matching the task via call_log FTS."""
    try:
        from codegraph.state.call_log import knowledge_search as _search

        query = _keyword_query(task)
        hits = _search(query, limit=limit)
        if not hits and query != task:
            hits = _search(task, limit=limit)
        return [
            KnowledgeHit(
                id=h["id"],
                kind=h["kind"],
                title=h["title"],
                body=(h["body"] or "")[:300],
                tags=h["tags"],
                score=h.get("score", 0.0),
            )
            for h in hits
        ]
    except Exception:
        return []


def _local_memory_hits(
    fts_conn: sqlite3.Connection, task: str, limit: int = 3
) -> list[MemoryDocHit]:
    """Wrap fts.memory_search with graceful fallback + keyword expansion."""
    try:
        from codegraph.core.fts import memory_search as _search

        query = _keyword_query(task)
        hits = _search(fts_conn, query, limit=limit)
        if not hits and query != task:
            hits = _search(fts_conn, task, limit=limit)
        return [
            MemoryDocHit(
                path=h.path,
                kind=h.kind,
                title=h.title,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ]
    except Exception:
        return []


def _local_plan_hits(
    fts_conn: sqlite3.Connection, task: str, limit: int = 2
) -> list[PlanDocHit]:
    """Wrap fts.plan_search with graceful fallback."""
    try:
        from codegraph.core.fts import plan_search as _search

        query = _keyword_query(task)
        hits = _search(fts_conn, query, limit=limit)
        if not hits and query != task:
            hits = _search(fts_conn, task, limit=limit)
        return [
            PlanDocHit(
                path=h.path,
                slug=h.slug,
                agent_id=h.agent_id,
                title=h.title,
                snippet=h.snippet,
                score=h.score,
            )
            for h in hits
        ]
    except Exception:
        return []


def render_context_markdown(ctx: TaskContext) -> str:
    """Render a TaskContext as compact Markdown for Claude."""
    lines = [f"## Context for: {ctx.task}", ""]

    for node in ctx.nodes:
        kind_icon = {
            "function": "fn",
            "class": "cls",
            "md_section": "doc",
            "tf_resource": "tf",
        }.get(node.kind, node.kind)
        lines.append(
            f"### [{kind_icon}] `{node.name}`, {node.file_path}:{node.start_line}"
        )
        if node.docstring:
            lines.append(f"> {node.docstring[:150]}")
        if node.relationships:
            lines.append(f"  Relationships: {', '.join(node.relationships)}")
        lines.append("")

    # Claude Code memory (user preferences / feedback / project notes)
    if ctx.memory_docs:
        lines.append("---")
        lines.append("## Memory (Claude Code)")
        lines.append("")
        for m in ctx.memory_docs:
            lines.append(f"- **[{m.kind}]** {m.title}")
            lines.append(f"  {m.snippet[:180]}")
            lines.append("")

    # Relevant past plans
    if ctx.plan_docs:
        lines.append("---")
        lines.append("## Related Plans")
        lines.append("")
        for p in ctx.plan_docs:
            agent = f" (agent {p.agent_id[:8]})" if p.agent_id else ""
            lines.append(f"- **{p.slug}**{agent}, {p.title}")
            lines.append(f"  `{p.path}`")
            lines.append("")

    # Persisted knowledge, patterns, decisions, gotchas
    if ctx.knowledge_docs:
        lines.append("---")
        lines.append("## Learned Knowledge")
        lines.append("")
        for k in ctx.knowledge_docs:
            tag_str = f"  [{', '.join(k.tags)}]" if k.tags else ""
            lines.append(f"- **[{k.kind}]** {k.title}{tag_str}")
            lines.append(f"  {k.body[:200]}")
            lines.append("")

    # Ruflo memory knowledge
    if ctx.memory_hits:
        lines.append("---")
        lines.append("## Project Knowledge (Ruflo Memory)")
        lines.append("")
        for hit in ctx.memory_hits:
            source_label = "memory" if hit.source == "ruflo_memory" else "pattern"
            lines.append(
                f"- **[{source_label}]** `{hit.key}` (sim: {hit.similarity:.2f})"
            )
            lines.append(f"  {hit.content[:200]}")
            lines.append("")

    lines.append(f"**Files**: {', '.join(ctx.files_referenced)}")
    lines.append(f"**Estimated tokens**: ~{ctx.token_estimate}")
    if ctx.memory_docs:
        lines.append(f"**Memory**: {len(ctx.memory_docs)} Claude Code entries")
    if ctx.plan_docs:
        lines.append(f"**Plans**: {len(ctx.plan_docs)} related")
    if ctx.knowledge_docs:
        lines.append(f"**Knowledge**: {len(ctx.knowledge_docs)} entries")
    if ctx.memory_hits:
        lines.append(f"**Ruflo knowledge**: {len(ctx.memory_hits)} entries")
    return "\n".join(lines)
