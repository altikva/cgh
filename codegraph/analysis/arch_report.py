# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: The architecture report: one Markdown page assembled from
#              what the graph already knows. collect_findings gathers, per
#              scope, the communities, pivot files (change-risk hotspots
#              when git history is there, import in-degree otherwise),
#              bridges (the only link between two communities, files that
#              reach many of them), unexpected links (layer inversions,
#              calls with no import behind them), import cycles and pivot
#              files no test imports. ask_questions turns those findings
#              into review questions by fixed rules; render_report writes
#              the page. Deterministic, no LLM, every section capped.

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from codegraph.analysis.communities import CommunityResult

# Layers an import should flow down through: a file may depend on a layer
# at its own rank or after it. presentation -> application -> domain, with
# infra beside domain (an adapter), so a domain or infra file importing
# application or presentation code points the wrong way.
_LAYER_RANK = {"presentation": 0, "application": 1, "domain": 2, "infra": 2}

# A file reaching this many communities besides its own is a bridge file.
BRIDGE_MIN_COMMUNITIES = 3
# A community this big keeping less than this share inside is "loose".
LOOSE_COHESION = 0.35
LOOSE_MIN_SIZE = 5
# A shared utility used by at least this share of the communities.
WIDE_HUB_SHARE = 0.5
# Questions per rule and scope, and in total.
QUESTIONS_PER_RULE = 2
MAX_QUESTIONS = 10


@dataclass(slots=True, frozen=True)
class Link:
    """One directed file->file link between two communities."""

    src: str
    dst: str
    src_community: int
    dst_community: int
    kind: str  # "import" or "calls"
    site: tuple[int, str, str] | None = None  # (line, caller, callee)


@dataclass(slots=True, frozen=True)
class Pivot:
    """A file the rest of the code leans on."""

    file: str
    importers: int
    degree: int
    communities: int
    commits: int | None
    score: float | None
    tested: bool


@dataclass(slots=True, frozen=True)
class BridgeFile:
    file: str
    community: int
    reaches: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class Cycle:
    files: tuple[str, ...]
    communities: tuple[int, ...]


@dataclass(slots=True)
class ScopeFindings:
    """Everything the report says about one scope."""

    scope: str
    result: CommunityResult
    pivots: list[Pivot] = field(default_factory=list)
    sole_links: list[Link] = field(default_factory=list)
    bridge_files: list[BridgeFile] = field(default_factory=list)
    inversions: list[Link] = field(default_factory=list)
    calls_only: list[Link] = field(default_factory=list)
    cycles: list[Cycle] = field(default_factory=list)
    pivots_ranked_by: str = "imports"

    def name(self, cid: int | None) -> str:
        for c in self.result.communities:
            if c.id == cid:
                return c.name
        return "shared utilities" if cid is None else f"#{cid}"


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def collect_findings(
    conn: Any,
    scope: str,
    result: CommunityResult,
    cycles: list[list[str]],
    hotspots: list[dict] | None = None,
    to_graph_path: Callable[[str], str] = lambda p: p,
    max_items: int = 10,
) -> ScopeFindings:
    """Gather the findings of one scope. ``hotspots`` is the change-risk
    ranking (repo-relative paths) when git history is available;
    ``to_graph_path`` maps those paths to the graph's."""
    from codegraph.analysis.impact import tests_for_file

    g = result.graph
    member = result.membership
    out = ScopeFindings(scope=scope, result=result)

    neighbours: dict[str, set[str]] = {}
    for a, b in g.weights:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    importers: Counter[str] = Counter(b for (_a, b) in g.imports)
    degree = g.degree()

    def reach(f: str) -> set[int]:
        return {member[n] for n in neighbours.get(f, ()) if n in member}

    # Pivots: the hotspot ranking where git has history, else in-degree.
    known = set(g.nodes)
    ranked: list[tuple[str, int | None, float | None]] = []
    if hotspots:
        out.pivots_ranked_by = "hotspots"
        for h in hotspots:
            path = to_graph_path(h["file"])
            if path in known:
                ranked.append((path, h.get("commits"), h.get("score")))
    else:
        order = sorted(known, key=lambda f: (-importers[f], -degree.get(f, 0), f))
        ranked = [(f, None, None) for f in order if importers[f] or degree.get(f)]
    for path, commits, score in ranked[:max_items]:
        out.pivots.append(
            Pivot(
                file=path,
                importers=importers[path],
                degree=degree.get(path, 0),
                communities=len(
                    reach(path) | ({member[path]} if path in member else set())
                ),
                commits=commits,
                score=score,
                tested=bool(tests_for_file(conn, path)),
            )
        )

    # Every directed link between two different communities.
    cross: dict[tuple[int, int], list[Link]] = {}
    # A call site resolved into several files is a dispatch through an
    # interface (one protocol, several implementations): calling it needs
    # no import of any implementation, so it is not an unexpected link.
    targets: Counter[tuple[str, int, str]] = Counter(
        (a, site[0], site[2]) for (a, _b), site in g.call_sites.items()
    )
    for (a, b), _n in sorted(g.imports.items()):
        ca, cb = member.get(a), member.get(b)
        if ca is not None and cb is not None and ca != cb:
            cross.setdefault(_pair(ca, cb), []).append(Link(a, b, ca, cb, "import"))
    for (a, b), _n in sorted(g.calls.items()):
        ca, cb = member.get(a), member.get(b)
        if ca is None or cb is None or ca == cb:
            continue
        link = Link(a, b, ca, cb, "calls", g.call_sites.get((a, b)))
        cross.setdefault(_pair(ca, cb), []).append(link)
        site = link.site
        dispatch = site is not None and targets[(a, site[0], site[2])] > 1
        if (a, b) not in g.imports and (b, a) not in g.imports and not dispatch:
            out.calls_only.append(link)

    # Bridges: two communities joined by one file pair, and nothing else.
    for _key, links in sorted(cross.items()):
        pairs = {_pair(lk.src, lk.dst) for lk in links}
        if len(pairs) == 1:
            out.sole_links.append(_preferred(links))
    for f in sorted(member):
        others = reach(f) - {member[f]}
        if len(others) >= BRIDGE_MIN_COMMUNITIES:
            out.bridge_files.append(BridgeFile(f, member[f], tuple(sorted(others))))
    out.bridge_files.sort(key=lambda b: (-len(b.reaches), b.file))

    # Layer inversions, over imports between known layers.
    for (a, b), _n in sorted(g.imports.items()):
        la = _LAYER_RANK.get(g.roles.get(a, ("", ""))[1])
        lb = _LAYER_RANK.get(g.roles.get(b, ("", ""))[1])
        if la is not None and lb is not None and la > lb:
            out.inversions.append(
                Link(a, b, member.get(a, -1), member.get(b, -1), "import")
            )

    for cyc in cycles:
        comms = tuple(sorted({member[f] for f in cyc if f in member}))
        out.cycles.append(Cycle(tuple(cyc), comms))
    # Cycles crossing communities first, then the biggest.
    out.cycles.sort(key=lambda c: (-len(c.communities), -len(c.files), c.files))
    return out


def _pair(a, b):
    return (a, b) if a <= b else (b, a)


def _preferred(links: list[Link]) -> Link:
    """An import over a call: it is the declared side of the link."""
    return sorted(links, key=lambda lk: (lk.kind != "import", lk.src, lk.dst))[0]


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def ask_questions(
    scopes: list[ScopeFindings], short: Callable[[str], str]
) -> list[str]:
    """Review questions from the findings, by fixed rules in a fixed order:
    sole links, layer inversions, cycles across communities, untested
    pivots, loose communities, wide shared utilities, calls with no import.
    At most ``QUESTIONS_PER_RULE`` per rule and scope."""
    rules: list[list[str]] = [[] for _ in range(7)]
    for s in scopes:
        tag = f" ({s.scope})" if len(scopes) > 1 else ""

        def q(rule: int, text: str, tag: str = tag) -> None:
            rules[rule].append(text + tag)

        for lk in s.sole_links[:QUESTIONS_PER_RULE]:
            q(
                0,
                f"`{short(lk.src)}` -> `{short(lk.dst)}` is the only link between "
                f"**{s.name(lk.src_community)}** and **{s.name(lk.dst_community)}**: "
                "is that intended, or is a shared piece missing?",
            )
        for lk in s.inversions[:QUESTIONS_PER_RULE]:
            la = s.result.graph.roles.get(lk.src, ("", ""))[1]
            lb = s.result.graph.roles.get(lk.dst, ("", ""))[1]
            q(
                1,
                f"`{short(lk.src)}` ({la}) imports `{short(lk.dst)}` ({lb}): "
                "should that dependency point the other way?",
            )
        for c in [c for c in s.cycles if len(c.communities) > 1][:QUESTIONS_PER_RULE]:
            names = ", ".join(f"**{s.name(x)}**" for x in c.communities)
            q(
                2,
                f"An import cycle of {len(c.files)} files ties {names} together: "
                "which edge would break it?",
            )
        for p in [p for p in s.pivots if not p.tested][:QUESTIONS_PER_RULE]:
            q(
                3,
                f"`{short(p.file)}` is a pivot ({p.importers} importers) that no "
                "test imports: is it covered some other way?",
            )
        loose = [
            c
            for c in s.result.communities
            if c.size >= LOOSE_MIN_SIZE and c.cohesion < LOOSE_COHESION
        ]
        for c in loose[:QUESTIONS_PER_RULE]:
            q(
                4,
                f"**{c.name}** keeps {round(c.cohesion * 100)}% of its links "
                "inside: one subsystem, or several sharing a directory?",
            )
        multi = max(1, sum(1 for c in s.result.communities if c.size > 1))
        for h in s.result.hubs[:QUESTIONS_PER_RULE]:
            if h.communities / multi >= WIDE_HUB_SHARE:
                q(
                    5,
                    f"`{short(h.file)}` is used by {h.communities} of {multi} "
                    "subsystems: a stable shared core, or a catch-all to split?",
                )
        for lk in s.calls_only[:QUESTIONS_PER_RULE]:
            where = (
                f"`{short(lk.src)}:{lk.site[0]}`" if lk.site else f"`{short(lk.src)}`"
            )
            callee = f" `{lk.site[2]}()`" if lk.site else ""
            q(
                6,
                f"{where} calls{callee} in `{short(lk.dst)}` without importing it: "
                "a real dependency, or two functions sharing a name?",
            )
    flat = [text for rule in rules for text in rule]
    return flat[:MAX_QUESTIONS]


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _link_text(lk: Link, short: Callable[[str], str]) -> str:
    if lk.kind == "calls" and lk.site:
        line, caller, callee = lk.site
        return (
            f"`{short(lk.src)}:{line}` `{caller}()` calls `{callee}()` "
            f"in `{short(lk.dst)}`"
        )
    return f"`{short(lk.src)}` imports `{short(lk.dst)}`"


def render_report(
    scopes: list[ScopeFindings],
    short: Callable[[str], str],
    title: str = "Architecture report",
    max_items: int = 10,
) -> tuple[str, list[str]]:
    """The report page and its questions. Sections are capped at
    ``max_items`` rows; a scope header is added only when there are
    several."""
    questions = ask_questions(scopes, short)
    lines = [f"# {title}", ""]
    lines.append(
        "Generated from the code graph (imports and calls), no LLM: the same "
        "index gives the same report. Test files are left out of the "
        "subsystems."
    )
    lines.append("")
    many = len(scopes) > 1
    h = "###" if many else "##"
    for s in scopes:
        r = s.result
        if many:
            lines += [f"## Scope `{s.scope}`", ""]
        multi = [c for c in r.communities if c.size > 1]
        lines += [
            f"{h} Overview",
            "",
            f"- {r.files_clustered} files clustered into {len(multi)} subsystems "
            f"(modularity {r.modularity}), {len(r.graph.weights)} file links.",
            f"- {len(r.hubs)} shared utilities, {len(s.cycles)} import cycles, "
            f"{len(s.sole_links)} single-link bridges, "
            f"{len(s.inversions) + len(s.calls_only)} unexpected links.",
            "",
        ]

        lines += [f"{h} Subsystems", ""]
        if multi:
            lines.append(
                "| # | Subsystem | Files | Cohesion | Central file | Linked to |"
            )
            lines.append("|---|---|---|---|---|---|")
            for c in multi[:max_items]:
                linked = (
                    ", ".join(f"{s.name(o)} ({w:.1f})" for o, w in c.links[:2]) or "-"
                )
                lines.append(
                    f"| {c.id} | {c.name} | {c.size} | {c.cohesion:.2f} | "
                    f"`{short(c.files[0])}` | {linked} |"
                )
            if len(multi) > max_items:
                lines.append(
                    f"\n{len(multi) - max_items} smaller subsystems not shown."
                )
            top = [(c, c.top_symbols[0]) for c in multi[:max_items] if c.top_symbols]
            if top:
                lines += ["", "Most called symbol of each:", ""]
                lines += [
                    f"- **{c.name}**: `{sym.name}` at `{short(sym.file)}:{sym.line}` "
                    f"({sym.callers} call sites)"
                    for c, sym in top
                ]
        else:
            lines.append("No subsystem of two files or more.")
        lines.append("")

        if r.hubs:
            lines += [f"{h} Shared utilities", ""]
            lines.append(
                "Linked to so many files that they were left out of the clustering."
            )
            lines.append("")
            lines += [
                f"- `{short(hub.file)}`: {hub.degree} neighbours, "
                f"{hub.communities} subsystems"
                for hub in r.hubs[:max_items]
            ]
            lines.append("")

        lines += [f"{h} Pivot files", ""]
        if s.pivots:
            how = (
                "change-risk hotspots (churn, importers, recency)"
                if s.pivots_ranked_by == "hotspots"
                else "import in-degree, no git history here"
            )
            lines.append(f"Ranked by {how}.")
            lines.append("")
            lines.append(
                "| File | Subsystem | Importers | Subsystems reached | Commits | Tested |"
            )
            lines.append("|---|---|---|---|---|---|")
            for p in s.pivots[:max_items]:
                commits = "-" if p.commits is None else str(p.commits)
                lines.append(
                    f"| `{short(p.file)}` | {s.name(r.membership.get(p.file))} | "
                    f"{p.importers} | {p.communities} | {commits} | "
                    f"{'yes' if p.tested else '**no**'} |"
                )
        else:
            lines.append("None found.")
        lines.append("")

        lines += [f"{h} Bridges", ""]
        if s.sole_links or s.bridge_files:
            for lk in s.sole_links[:max_items]:
                lines.append(
                    f"- Only link between **{s.name(lk.src_community)}** and "
                    f"**{s.name(lk.dst_community)}**: {_link_text(lk, short)}"
                )
            for b in s.bridge_files[:max_items]:
                reached = ", ".join(s.name(x) for x in b.reaches[:4])
                more = f" and {len(b.reaches) - 4} more" if len(b.reaches) > 4 else ""
                lines.append(
                    f"- `{short(b.file)}` ({s.name(b.community)}) reaches "
                    f"{len(b.reaches)} other subsystems: {reached}{more}"
                )
        else:
            lines.append(
                "None: every pair of subsystems that talks does so through several files."
            )
        lines.append("")

        lines += [f"{h} Unexpected links", ""]
        if s.inversions or s.calls_only:
            for lk in s.inversions[:max_items]:
                la = r.graph.roles.get(lk.src, ("", ""))[1]
                lb = r.graph.roles.get(lk.dst, ("", ""))[1]
                lines.append(
                    f"- Layer inversion ({la} -> {lb}): {_link_text(lk, short)}"
                )
            for lk in s.calls_only[:max_items]:
                lines.append(f"- Call with no import: {_link_text(lk, short)}")
            hidden = max(0, len(s.inversions) - max_items) + max(
                0, len(s.calls_only) - max_items
            )
            if hidden:
                lines.append(f"- ... and {hidden} more.")
        else:
            lines.append("None found.")
        lines.append("")

        lines += [f"{h} Import cycles", ""]
        if s.cycles:
            for c in s.cycles[:max_items]:
                files = ", ".join(f"`{short(f)}`" for f in c.files[:6])
                more = f" and {len(c.files) - 6} more" if len(c.files) > 6 else ""
                across = (
                    " across " + ", ".join(s.name(x) for x in c.communities)
                    if len(c.communities) > 1
                    else ""
                )
                lines.append(f"- {len(c.files)} files{across}: {files}{more}")
            if len(s.cycles) > max_items:
                lines.append(f"- ... and {len(s.cycles) - max_items} more.")
        else:
            lines.append("None.")
        lines.append("")

    lines += ["## Questions worth asking", ""]
    if questions:
        lines += [f"{i}. {q}" for i, q in enumerate(questions, start=1)]
    else:
        lines.append("Nothing stands out.")
    lines.append("")
    return "\n".join(lines), questions


def parent_hotspots(root: Any, conn: Any) -> list[dict] | None:
    """The change-risk ranking of the parent repo, or None without commit
    history. Same scoring as the hotspots tool."""
    import time

    from codegraph.analysis import churn as _churn

    history = _churn.file_churn(root)
    if not history:
        return None
    importers: Counter[str] = Counter()
    for row in conn.find_neighbors("IMPORTS", return_dst=["path"]):
        if row.get("dst_path"):
            importers[row["dst_path"]] += 1
    return _churn.rank_hotspots(
        history, dict(importers), _to_graph(root), now=time.time()
    )


def _to_graph(root: Any) -> Callable[[str], str]:
    import os

    def to_graph(rel: str) -> str:
        if os.path.isabs(rel) or root is None:
            return rel
        return os.path.join(str(root), rel)

    return to_graph


def scope_findings(
    conn: Any,
    scope: str,
    resolution: float = 1.0,
    hotspots: list[dict] | None = None,
    root: Any = None,
    max_items: int = 10,
) -> ScopeFindings:
    """Communities, cycles and findings of the graph behind ``conn``."""
    from codegraph.analysis.communities import communities_for
    from codegraph.analysis.cycles import find_import_cycles

    return collect_findings(
        conn,
        scope,
        communities_for(conn, resolution),
        find_import_cycles(conn),
        hotspots=hotspots,
        to_graph_path=_to_graph(root),
        max_items=max_items,
    )


def build_report(
    parent_conn: Any,
    root: Any,
    short: Callable[[str], str],
    resolution: float = 1.0,
    max_items: int = 10,
) -> dict:
    """The full report over the parent and each federated child, as the
    JSON the MCP tool and the CLI share: {markdown, questions, scopes,
    warnings?}. ``parent_conn`` None means the parent graph could not be
    opened; the children still report."""
    from pathlib import Path

    from codegraph.analysis.federation import for_each_child_graphdb

    scopes: list[ScopeFindings] = []
    warnings: list[dict] = []
    if parent_conn is not None:
        try:
            hot = parent_hotspots(root, parent_conn) if root is not None else None
            scopes.append(
                scope_findings(parent_conn, "parent", resolution, hot, root, max_items)
            )
        except Exception as exc:
            warnings.append(
                {"scope": "parent", "error": f"{type(exc).__name__}: {exc}"}
            )
    else:
        warnings.append({"scope": "parent", "error": "graph DB is locked (indexing?)"})
    if root is not None:
        children = for_each_child_graphdb(
            root,
            lambda c, child_root: scope_findings(
                c, "", resolution, None, child_root, max_items
            ),
        )
        for res in children:
            if res.error or res.payload is None:
                warnings.append({"scope": res.scope, "error": res.error or "no data"})
                continue
            res.payload.scope = res.scope
            scopes.append(res.payload)
    title = "Architecture report"
    if root is not None:
        title += f": {Path(str(root)).name}"
    markdown, questions = render_report(scopes, short, title, max_items)
    payload: dict = {
        "markdown": markdown,
        "questions": questions,
        "scopes": report_summary(scopes),
    }
    if warnings:
        payload["partial"] = True
        payload["warnings"] = warnings
    return payload


def report_summary(scopes: list[ScopeFindings]) -> list[dict]:
    """Per-scope counts behind the report, for the JSON answer."""
    return [
        {
            "scope": s.scope,
            "subsystems": sum(1 for c in s.result.communities if c.size > 1),
            "shared_utilities": len(s.result.hubs),
            "pivots": len(s.pivots),
            "untested_pivots": sum(1 for p in s.pivots if not p.tested),
            "sole_links": len(s.sole_links),
            "bridge_files": len(s.bridge_files),
            "layer_inversions": len(s.inversions),
            "calls_without_import": len(s.calls_only),
            "import_cycles": len(s.cycles),
        }
        for s in scopes
    ]
