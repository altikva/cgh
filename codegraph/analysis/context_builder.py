# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-11
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __contributors__ = ["jndjama (Joy Ndjama)"]
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# __maintainer__ = "jndjama (Joy Ndjama)"
# __email__ = "joy.ndjama@altikva.com"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Builds a compact, ranked context block from graph + FTS
#              for a natural-language task description, pre-computing the
#              relevant subgraph so the agent skips several searches and reads.
#              Each symbol carries its line range and a few lines saying why
#              it was picked, the files come in two tiers (start here,
#              related) and a next step names the one call to make next.
#              Paths are relative to the root of the repo they come from.

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from codegraph.core.fts import STOPWORDS, fold_accents, fts_search

# A justification line is kept to about this many characters.
WHY_MAX_CHARS = 160
# At most this many justification lines per symbol.
WHY_MAX_LINES = 3
# At most this many files in the start_here tier.
START_HERE_MAX = 3
# A ranged Read suggested by ``next`` covers at most this many lines.
NEXT_READ_MAX_LINES = 80
# Source files larger than this are not read for justification lines.
_READ_MAX_BYTES = 2_000_000


def rel_to(path: str, root: str | Path | None) -> str:
    """``path`` relative to ``root`` when it lies under it, else unchanged."""
    if not root or not path:
        return path
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def _clip(text: str, limit: int = WHY_MAX_CHARS) -> str:
    """``text`` on one line, cut to ``limit`` characters with an ellipsis."""
    one = " ".join(text.split())
    if len(one) <= limit:
        return one
    return one[: limit - 3].rstrip() + "..."


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
    # Rationale comments attached to the symbol (analysis/rationale.py),
    # rows over graph_model.RATIONALE_COLUMNS, a few at most.
    rationale: list[dict] = field(default_factory=list)
    # The repo the symbol was found in: "parent", or a federated child by
    # name, and that repo's root, which its displayed path is relative to.
    scope: str = "parent"
    scope_root: str = ""
    # One to three short lines saying why the symbol was picked: the
    # docstring sentence that matches the task, a call site, or the source
    # line of the symbol that matches it best.
    why: list[str] = field(default_factory=list)

    @property
    def rel_path(self) -> str:
        """The file path relative to the root of the node's scope."""
        return rel_to(self.file_path, self.scope_root)

    @property
    def label(self) -> str:
        """``rel_path``, prefixed by the scope name for a federated child."""
        if self.scope == "parent":
            return self.rel_path
        return f"{self.scope}:{self.rel_path}"


@dataclass
class MemoryDocHit:
    """A hit from the local Claude Code memory index."""

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
    memory_docs: list[MemoryDocHit] = field(default_factory=list)
    plan_docs: list[PlanDocHit] = field(default_factory=list)
    knowledge_docs: list[KnowledgeHit] = field(default_factory=list)


# Shared with the free-text search: English + French, compared without
# accents (see codegraph.core.fts.prose_terms).
_STOPWORDS = STOPWORDS


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
                if len(q) >= min_len and fold_accents(q) not in _STOPWORDS:
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


def task_terms(task: str) -> list[str]:
    """The keywords of ``task``, lowercased and without accents, the same
    ones the FTS query is built from."""
    query = _keyword_query(task)
    if query == task and " OR " not in query:
        words = re.findall(r"[^\W\d_]{3,}", task, flags=re.UNICODE)
        return [fold_accents(w.lower()) for w in words]
    return [fold_accents(t.lower()) for t in query.split(" OR ")]


def code_nodes(
    task: str,
    graph_conn,
    fts_conn: sqlite3.Connection,
    max_nodes: int = 15,
    root: str | Path | None = None,
    scope: str = "parent",
) -> list[ContextNode]:
    """
    The ranked symbols of one repo for a natural-language task.
    1. Keyword-extract the task, FTS-search to find initial seed symbols
    2. Expand via graph edges (callers, callees, inheritance)
    3. Rank by relevance, keep the top N, give each its justification lines
    """
    # Natural-language sentences don't match symbol names directly, extract
    # keywords first.
    query = _keyword_query(task)
    fts_results = fts_search(fts_conn, query, limit=max_nodes * 2)
    if not fts_results and query != task:
        # Safety net: retry with the raw task if keyword query was too narrow
        fts_results = fts_search(fts_conn, task, limit=max_nodes * 2)

    nodes: list[ContextNode] = []
    seen_ids: set[str] = set()
    top = fts_results[0].score if fts_results and fts_results[0].score > 0 else 1
    for r in fts_results:
        node_id = f"{r.file_path}:{r.start_line}"
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        nodes.append(
            ContextNode(
                kind=r.kind,
                name=r.name,
                file_path=r.file_path,
                start_line=r.start_line,
                end_line=r.end_line,
                docstring=r.docstring,
                relevance=min(r.score / top, 1.0),
                scope=scope,
                scope_root=str(root) if root else "",
            )
        )

    nodes.sort(key=lambda n: n.relevance, reverse=True)
    nodes = nodes[:max_nodes]

    terms = task_terms(task)
    lines = _LineCache()
    for node in nodes:
        callers = _expand(graph_conn, node)
        node.why = _justify(node, terms, callers, lines)
    _attach_rationale(graph_conn, nodes)
    return nodes


def _expand(graph_conn, node: ContextNode) -> list[dict]:
    """Fill the graph relationships of ``node`` (callers, callees, parent
    classes, three each); returns the caller rows, with their location."""
    callers: list[dict] = []
    if node.kind in ("function", "method"):
        callers = graph_conn.find_neighbors(
            "CALLS",
            dst_where={"name": node.name},
            return_src=["name", "file_path", "start_line", "end_line"],
        )[:3]
        callees = graph_conn.find_neighbors(
            "CALLS",
            src_where={"name": node.name},
            return_dst=["name"],
        )[:3]
        if callers:
            names = ", ".join(c["src_name"] for c in callers)
            node.relationships.append(f"called by {names}")
        if callees:
            names = ", ".join(c["dst_name"] for c in callees)
            node.relationships.append(f"calls {names}")
    elif node.kind == "class":
        parents = graph_conn.find_neighbors(
            "INHERITS",
            src_where={"name": node.name},
            return_dst=["name"],
        )[:3]
        if parents:
            names = ", ".join(p["dst_name"] for p in parents)
            node.relationships.append(f"extends {names}")
    return callers


class _LineCache:
    """The lines of the source files read for one answer, each file once."""

    def __init__(self) -> None:
        self._files: dict[str, list[str] | None] = {}

    def lines(self, path: str) -> list[str] | None:
        if path not in self._files:
            self._files[path] = _read_lines(path)
        return self._files[path]


def _read_lines(path: str) -> list[str] | None:
    """The lines of ``path``, or None when it is missing, unreadable or too
    large to read for a few justification lines."""
    try:
        p = Path(path)
        if p.stat().st_size > _READ_MAX_BYTES:
            return None
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def _hits(text: str, terms: list[str]) -> int:
    """How many of ``terms`` occur in ``text``, compared without case or
    accents."""
    folded = fold_accents(text.lower())
    return sum(1 for t in terms if t in folded)


# A sentence ends at . ! or ? followed by a capital, or at a blank line; not
# after "e.g.", "i.e." or a list number such as "1.".
_SENTENCE_END = re.compile(
    r"(?<!e\.g\.)(?<!i\.e\.)(?<!\d\.)(?<=[.!?])\s+(?=[A-Z])|\n\s*\n"
)


def _doc_line(docstring: str, terms: list[str]) -> str:
    """The sentence of ``docstring`` that matches the task best, the first
    one when none matches."""
    parts = [
        s.strip()
        for s in _SENTENCE_END.split(docstring or "")
        if s.strip() and len(s.strip()) > 2
    ]
    if not parts:
        return ""
    best = max(parts, key=lambda s: (_hits(s, terms), -parts.index(s)))
    return best


def _call_site(node: ContextNode, callers: list[dict], lines: _LineCache) -> str:
    """The first line of a caller that calls ``node``, as ``path:line code``,
    a caller outside the tests first."""
    pattern = re.compile(rf"(?<![\w]){re.escape(node.name)}\s*\(")
    ordered = sorted(callers, key=lambda c: _is_test(c.get("src_file_path") or ""))
    for c in ordered:
        path = c.get("src_file_path") or ""
        src = lines.lines(path) if path else None
        if not src:
            continue
        start = max(int(c.get("src_start_line") or 1), 1)
        end = int(c.get("src_end_line") or start)
        for i in range(start, min(end, len(src)) + 1):
            text = src[i - 1]
            if pattern.search(text) and not text.lstrip().startswith(("def ", "#")):
                where = rel_to(path, node.scope_root)
                return f"{where}:{i} {text.strip()}"
    return ""


def _body_line(node: ContextNode, terms: list[str], lines: _LineCache) -> str:
    """The source line of ``node`` that matches the task best, its first
    line (the signature or heading) when none does."""
    src = lines.lines(node.file_path)
    if not src or node.start_line < 1 or node.start_line > len(src):
        return ""
    start = node.start_line
    end = min(max(node.end_line or start, start), len(src), start + 200)
    best, best_hits = start, 0
    for i in range(start + 1, end + 1):
        n = _hits(src[i - 1], terms)
        if n > best_hits:
            best, best_hits = i, n
    text = src[best - 1].strip()
    return f"L{best}: {text}" if text else ""


def _justify(
    node: ContextNode,
    terms: list[str],
    callers: list[dict],
    lines: _LineCache,
) -> list[str]:
    """One to three short lines saying why ``node`` was picked: the docstring
    sentence matching the task, a call site, and, when there is no docstring
    or it does not match, the best-matching source line."""
    why: list[str] = []
    doc = _doc_line(node.docstring, terms)
    if doc:
        why.append(_clip(f"doc: {doc}"))
    call = _call_site(node, callers, lines) if callers else ""
    if call:
        why.append(_clip(f"call: {call}"))
    if not doc or not _hits(doc, terms):
        body = _body_line(node, terms, lines)
        if body and len(why) < WHY_MAX_LINES:
            why.append(_clip(body))
    if not why:
        matched = [t for t in terms if t in fold_accents(node.name.lower())]
        if matched:
            why.append(_clip(f"name matches: {', '.join(matched)}"))
    return why[:WHY_MAX_LINES]


def _is_test(path: str) -> bool:
    """Whether ``path`` looks like a test file."""
    parts = Path(path).parts
    name = parts[-1] if parts else ""
    return (
        "tests" in parts
        or "test" in parts
        or "__tests__" in parts
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".spec.ts", "_test.go"))
    )


_CODE_KINDS = {"function", "method", "class", "tf_resource", "endpoint"}


def file_tiers(nodes: list[ContextNode]) -> tuple[list[str], list[str]]:
    """The files of ``nodes`` split into ``start_here`` (at most
    START_HERE_MAX, the best entry points) and ``related``, each best first.

    A file scores its best symbol plus a share of the others; a test file
    counts for half, and so does a file holding only docs or data, so the
    code the task is about leads."""
    relevances: dict[str, list[float]] = {}
    code: dict[str, bool] = {}
    for n in nodes:
        relevances.setdefault(n.label, []).append(n.relevance)
        code[n.label] = code.get(n.label, False) or n.kind in _CODE_KINDS
    scores: dict[str, float] = {}
    for key, rs in relevances.items():
        top = max(rs)
        scores[key] = top + 0.3 * (sum(rs) - top)
        if _is_test(key.split(":", 1)[-1]):
            scores[key] *= 0.5
        if not code[key]:
            scores[key] *= 0.5
    ranked = sorted(scores, key=lambda k: (-scores[k], k))
    return ranked[:START_HERE_MAX], ranked[START_HERE_MAX:]


def next_step(
    nodes: list[ContextNode],
    start_here: list[str],
    task: str,
    root: str | Path | None = None,
) -> str:
    """The one call to make next: a ranged Read of the best symbol of the
    first start_here file, or a free-text search when nothing matched.

    The path of a federated child's file is given from ``root`` (the
    parent), so the Read works from the parent's working tree."""
    if nodes and start_here:
        first = start_here[0]
        in_file = [n for n in nodes if n.label == first]
        node = max(in_file, key=lambda n: n.relevance) if in_file else nodes[0]
        path = node.rel_path
        if node.scope != "parent" and root:
            path = os.path.relpath(node.file_path, root)
        start = max(node.start_line, 1)
        span = (node.end_line or start) - start + 1
        limit = min(max(span, 20), NEXT_READ_MAX_LINES)
        return f"Read {path} offset={start} limit={limit}"
    terms = task_terms(task)[:3]
    words = " ".join(terms) if terms else task
    return f'fts_search(query="{words}")'


def merge_nodes(
    nodes: list[ContextNode], extra: list[ContextNode], max_nodes: int
) -> list[ContextNode]:
    """``nodes`` and ``extra`` (a federated child's) in one ranking, the
    best ``max_nodes``. Relevance is normalized per scope, so the best
    symbol of each scope competes at 1.0."""
    merged = sorted(nodes + extra, key=lambda n: n.relevance, reverse=True)
    return merged[:max_nodes]


def token_estimate(nodes: list[ContextNode]) -> int:
    """Rough tokens of the symbols' part of the answer, about 4 characters
    a token."""
    total = sum(
        len(n.name) + len(n.rel_path) + sum(len(w) for w in n.why) + 50 for n in nodes
    )
    return total // 4


def context_for_task(
    task: str,
    graph_conn,
    fts_conn: sqlite3.Connection,
    max_nodes: int = 15,
    root: str | Path | None = None,
) -> TaskContext:
    """
    Build a ranked context for a natural-language task: the repo's best
    symbols (see ``code_nodes``) plus the matching memory, plans and
    knowledge entries. ``root`` is the repo root the displayed paths are
    relative to.
    """
    # Query local Claude Code memory + plans (SQLite FTS, always on)
    memory_docs = _local_memory_hits(fts_conn, task, limit=3)
    plan_docs = _local_plan_hits(fts_conn, task, limit=2)
    knowledge_docs = _local_knowledge_hits(task, limit=3)

    nodes = code_nodes(task, graph_conn, fts_conn, max_nodes=max_nodes, root=root)
    files = sorted(set(n.file_path for n in nodes))

    return TaskContext(
        task=task,
        nodes=nodes,
        files_referenced=files,
        token_estimate=token_estimate(nodes),
        memory_docs=memory_docs,
        plan_docs=plan_docs,
        knowledge_docs=knowledge_docs,
    )


def _attach_rationale(graph_conn, nodes: list[ContextNode]) -> None:
    """Give the function and class nodes their rationale comments, a few per
    node and a few in all, best first. Best effort: an index without the
    rationale table, or any read failure, leaves the context without them."""
    from codegraph.analysis.rationale import (
        CONTEXT_PER_NODE,
        CONTEXT_TOTAL,
        for_definitions,
    )

    defs = [
        (n.file_path, n.start_line)
        for n in nodes
        if n.kind in ("function", "method", "class")
    ]
    if not defs:
        return
    try:
        found = for_definitions(graph_conn, defs)
    except Exception:  # best effort, see the docstring
        return
    budget = CONTEXT_TOTAL
    for n in nodes:
        rows = found.get((n.file_path, n.start_line), [])[
            : min(CONTEXT_PER_NODE, budget)
        ]
        n.rationale = rows
        budget -= len(rows)
        if budget <= 0:
            break


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
            "method": "fn",
            "class": "cls",
            "md_section": "doc",
            "tf_resource": "tf",
        }.get(node.kind, node.kind)
        span = (
            f"{node.start_line}-{node.end_line}"
            if node.end_line and node.end_line > node.start_line
            else str(node.start_line)
        )
        scope = f" ({node.scope})" if node.scope != "parent" else ""
        lines.append(f"### [{kind_icon}] `{node.name}`{scope} {node.rel_path}:{span}")
        for w in node.why:
            lines.append(f"- {w}")
        if node.relationships:
            lines.append(f"  {'; '.join(node.relationships)}")
        for r in node.rationale:
            text = (r.get("text") or "")[:160]
            adr = f" ({r['adr']})" if r.get("adr") and r["adr"] not in text else ""
            lines.append(f"  {r['tag']} (line {r['line']}): {text}{adr}")
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
            lines.append(f"  `{_home_short(p.path)}`")
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

    # The files, their tiers and the next step are top-level keys of the
    # answer, not repeated here.
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _home_short(path: str) -> str:
    """``path`` with the home directory written ``~``: a plan or memory
    file lives outside the repo, so it cannot be repo-relative."""
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path
