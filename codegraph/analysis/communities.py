# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Subsystem detection from how the code actually connects.
#              build_file_graph folds IMPORTS and CALLS into weighted
#              file->file edges, find_hubs sets aside the utility files
#              everything leans on, louvain clusters the rest (pure
#              Python, deterministic, resolution parameter), and
#              detect_communities names each cluster from its own content
#              (dominant directory, frequent file-name tokens, central
#              file). Results are cached per index state; communities_payload
#              turns per-scope results into the JSON the MCP tool and the
#              CLI share.

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# A file is a shared utility (left out of clustering) when its distinct
# neighbour count reaches the 99th percentile of the graph and this floor.
# The floor keeps small graphs from losing an ordinary well-linked file.
HUB_MIN_DEGREE = 10
HUB_PERCENTILE = 0.99

# Edge weights. An import is a declared dependency and counts in full; CALLS
# edges are name-matched best effort, so they add a log-compressed bonus that
# strengthens a link without letting one chatty pair dominate.
_IMPORT_WEIGHT = 1.0
_CALL_WEIGHT = 0.5

# Louvain: passes over the nodes per level, and the gain a move must beat.
_MAX_PASSES = 64
_MAX_LEVELS = 32
_EPS = 1e-12

# Naming: a directory names a community when it holds this share of its files.
_DIR_SHARE = 0.6
_NAME_STOPWORDS = frozenset(
    {
        "and",
        "for",
        "from",
        "index",
        "init",
        "lib",
        "main",
        "mod",
        "spec",
        "src",
        "test",
        "tests",
        "the",
        "with",
    }
)

# Output caps.
FILES_CAP = 10
SYMBOLS_CAP = 5
LINKS_CAP = 3
HUBS_CAP = 15

_CACHE_SIZE = 16


@dataclass(slots=True, frozen=True)
class SymbolRef:
    """A function in a community, ranked by how many call sites reach it."""

    name: str
    file: str
    line: int
    callers: int


@dataclass(slots=True, frozen=True)
class Hub:
    """A shared utility file left out of clustering."""

    file: str
    degree: int
    communities: int


@dataclass(slots=True, frozen=True)
class Community:
    """One detected subsystem. ``files`` is ordered most central first."""

    id: int
    name: str
    files: tuple[str, ...]
    internal_weight: float
    external_weight: float
    top_symbols: tuple[SymbolRef, ...]
    links: tuple[tuple[int, float], ...]

    @property
    def size(self) -> int:
        return len(self.files)

    @property
    def cohesion(self) -> float:
        """Share of this community's edge weight that stays inside it."""
        total = self.internal_weight + self.external_weight
        return round(self.internal_weight / total, 3) if total else 0.0


@dataclass(slots=True)
class FileGraph:
    """The file-level graph clustering runs on.

    ``weights`` is undirected, keyed by the sorted path pair. ``imports`` and
    ``calls`` keep the directed counts behind it, for reports that need to
    say which way a link points. ``roles`` maps each file to (role, layer).
    """

    nodes: list[str] = field(default_factory=list)
    weights: dict[tuple[str, str], float] = field(default_factory=dict)
    imports: dict[tuple[str, str], int] = field(default_factory=dict)
    calls: dict[tuple[str, str], int] = field(default_factory=dict)
    roles: dict[str, tuple[str, str]] = field(default_factory=dict)
    symbols: list[SymbolRef] = field(default_factory=list)

    def degree(self) -> dict[str, int]:
        """Distinct neighbour count per file."""
        deg: Counter[str] = Counter()
        for a, b in self.weights:
            deg[a] += 1
            deg[b] += 1
        return dict(deg)


@dataclass(slots=True)
class CommunityResult:
    """Everything one clustering run produced for one scope."""

    resolution: float
    communities: list[Community]
    hubs: list[Hub]
    membership: dict[str, int]
    graph: FileGraph
    modularity: float
    files_clustered: int
    singletons: int


# ---------------------------------------------------------------------------
# Graph building
# ---------------------------------------------------------------------------


def build_file_graph(conn: Any, files: list[dict] | None = None) -> FileGraph:
    """Fold IMPORTS and CALLS into weighted file->file edges.

    Test files (role ``test``) are left out: they attach to whatever they
    test and would blur the subsystems; ``tests_for`` maps them instead.
    Self links (a file calling itself) carry no clustering signal and are
    dropped. ``files`` is the File rows when the caller already has them.
    """
    if files is None:
        files = _file_rows(conn)
    roles = {
        r["path"]: ((r.get("role") or "other"), (r.get("layer") or "other"))
        for r in files
    }
    keep = {p for p, (role, _layer) in roles.items() if role != "test"}

    imports: Counter[tuple[str, str]] = Counter()
    for row in conn.find_neighbors("IMPORTS", return_src=["path"], return_dst=["path"]):
        a, b = row.get("src_path"), row.get("dst_path")
        if a and b and a != b and a in keep and b in keep:
            imports[(a, b)] += 1

    calls: Counter[tuple[str, str]] = Counter()
    in_degree: Counter[tuple[str, str, int]] = Counter()
    for row in conn.find_neighbors(
        "CALLS",
        return_src=["file_path"],
        return_dst=["file_path", "name", "start_line"],
    ):
        a, b = row.get("src_file_path"), row.get("dst_file_path")
        if not a or not b:
            continue
        if b in keep:
            in_degree[
                (row.get("dst_name") or "?", b, int(row.get("dst_start_line") or 0))
            ] += 1
        if a != b and a in keep and b in keep:
            calls[(a, b)] += 1

    pair_imports: Counter[tuple[str, str]] = Counter()
    pair_calls: Counter[tuple[str, str]] = Counter()
    for (a, b), n in imports.items():
        pair_imports[_pair(a, b)] += n
    for (a, b), n in calls.items():
        pair_calls[_pair(a, b)] += n
    weights: dict[tuple[str, str], float] = {}
    for key in sorted(set(pair_imports) | set(pair_calls)):
        w = 0.0
        if pair_imports.get(key):
            w += _IMPORT_WEIGHT
        if pair_calls.get(key):
            w += _CALL_WEIGHT * math.log2(1 + pair_calls[key])
        weights[key] = round(w, 6)

    nodes = sorted({p for key in weights for p in key})
    symbols = [
        SymbolRef(name=name, file=path, line=line, callers=n)
        for (name, path, line), n in in_degree.items()
    ]
    symbols.sort(key=lambda s: (-s.callers, s.file, s.line, s.name))
    return FileGraph(
        nodes=nodes,
        weights=weights,
        imports=dict(imports),
        calls=dict(calls),
        roles=roles,
        symbols=symbols,
    )


def _file_rows(conn: Any) -> list[dict]:
    return conn.find_nodes(
        "File", return_fields=["path", "mtime", "git_blob_sha", "role", "layer"]
    )


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# Hubs
# ---------------------------------------------------------------------------


def find_hubs(
    degree: dict[str, int],
    min_degree: int = HUB_MIN_DEGREE,
    percentile: float = HUB_PERCENTILE,
) -> set[str]:
    """Files whose neighbour count reaches the ``percentile`` rank and
    ``min_degree``. These are the utilities everything leans on; left in,
    they glue unrelated subsystems into one."""
    if not degree:
        return set()
    values = sorted(degree.values())
    rank = min(len(values) - 1, int(percentile * len(values)))
    threshold = max(min_degree, values[rank])
    return {p for p, d in degree.items() if d >= threshold}


# ---------------------------------------------------------------------------
# Louvain
# ---------------------------------------------------------------------------


def louvain(
    n: int, edges: dict[tuple[int, int], float], resolution: float = 1.0
) -> list[int]:
    """Community label per node, by modularity optimisation (Louvain).

    Deterministic: nodes are visited in index order, candidate communities
    in label order, and a move must strictly beat staying put, so the same
    graph always gives the same partition. ``resolution`` above 1 favours
    smaller communities, below 1 larger ones. Labels are renumbered by the
    first node of each community.
    """
    adj: list[dict[int, float]] = [{} for _ in range(n)]
    for (i, j), w in sorted(edges.items()):
        if i == j or w <= 0:
            continue
        adj[i][j] = adj[i].get(j, 0.0) + w
        adj[j][i] = adj[j].get(i, 0.0) + w
    membership = list(range(n))
    for _level in range(_MAX_LEVELS):
        comm, moved = _one_level(adj, resolution)
        if not moved:
            break
        remap: dict[int, int] = {}
        for c in comm:
            remap.setdefault(c, len(remap))
        comm = [remap[c] for c in comm]
        membership = [comm[m] for m in membership]
        if len(remap) == len(adj):
            break
        agg: list[dict[int, float]] = [{} for _ in range(len(remap))]
        for i, nbrs in enumerate(adj):
            ci = comm[i]
            row = agg[ci]
            for j, w in nbrs.items():
                cj = comm[j]
                row[cj] = row.get(cj, 0.0) + w
        adj = agg
    return _renumber(membership)


def _one_level(
    adj: list[dict[int, float]], resolution: float
) -> tuple[list[int], bool]:
    """Local moving phase: move each node to the neighbouring community
    with the best modularity gain until no move helps."""
    n = len(adj)
    k = [sum(nbrs.values()) for nbrs in adj]
    m2 = sum(k)
    comm = list(range(n))
    if m2 <= 0:
        return comm, False
    tot = k[:]
    moved_any = False
    for _pass in range(_MAX_PASSES):
        moved = False
        for i in range(n):
            ki = k[i]
            if ki <= 0:
                continue
            ci = comm[i]
            links: dict[int, float] = {}
            for j, w in adj[i].items():
                if j != i:
                    links[comm[j]] = links.get(comm[j], 0.0) + w
            tot[ci] -= ki
            best_c = ci
            best_gain = links.get(ci, 0.0) - resolution * tot[ci] * ki / m2
            for c in sorted(links):
                if c == ci:
                    continue
                gain = links[c] - resolution * tot[c] * ki / m2
                if gain > best_gain + _EPS:
                    best_gain, best_c = gain, c
            tot[best_c] += ki
            if best_c != ci:
                comm[i] = best_c
                moved = True
                moved_any = True
        if not moved:
            break
    return comm, moved_any


def _renumber(labels: list[int]) -> list[int]:
    remap: dict[int, int] = {}
    return [remap.setdefault(c, len(remap)) for c in labels]


def split_disconnected(labels: list[int], adj: list[dict[int, float]]) -> list[int]:
    """Split every community into its connected parts. Louvain can leave a
    community whose members only met through a node that later moved away;
    a subsystem should at least hang together."""
    out = [-1] * len(labels)
    nxt = 0
    for start in range(len(labels)):
        if out[start] != -1:
            continue
        out[start] = nxt
        stack = [start]
        while stack:
            i = stack.pop()
            for j in adj[i]:
                if out[j] == -1 and labels[j] == labels[start]:
                    out[j] = nxt
                    stack.append(j)
        nxt += 1
    return out


def modularity(
    labels: list[int], adj: list[dict[int, float]], resolution: float = 1.0
) -> float:
    """Newman modularity of a partition of an undirected weighted graph."""
    k = [sum(nbrs.values()) for nbrs in adj]
    m2 = sum(k)
    if m2 <= 0:
        return 0.0
    inside: Counter[int] = Counter()
    tot: Counter[int] = Counter()
    for i, nbrs in enumerate(adj):
        tot[labels[i]] += k[i]
        for j, w in nbrs.items():
            if labels[i] == labels[j]:
                inside[labels[i]] += w
    q = sum(inside[c] / m2 - resolution * (tot[c] / m2) ** 2 for c in tot)
    return round(q, 4)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

_TOKEN_SPLIT = re.compile(r"[_\-.\s]+|(?<=[a-z0-9])(?=[A-Z])")


def name_tokens(text: str) -> list[str]:
    """Lowercase words of a file stem or directory name."""
    out = []
    for t in _TOKEN_SPLIT.split(text):
        t = t.lower()
        if len(t) >= 3 and not t.isdigit() and t not in _NAME_STOPWORDS:
            out.append(t)
    return out


def _rel_dir(path: str, base: str) -> str:
    rel = (
        os.path.relpath(os.path.dirname(path), base) if base else os.path.dirname(path)
    )
    rel = rel.replace(os.sep, "/")
    return "" if rel == "." else rel


def _prefix_counts(rel_dirs: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for d in rel_dirs:
        parts = d.split("/") if d else []
        for depth in range(1, len(parts) + 1):
            counts["/".join(parts[:depth])] += 1
    return counts


def dominant_dir(rel_dirs: list[str], share: float = _DIR_SHARE) -> str:
    """Deepest directory holding at least ``share`` of the files. Returns ""
    when only the top level does."""
    n = len(rel_dirs)
    if not n:
        return ""
    counts = _prefix_counts(rel_dirs)
    # With a share above one half, two directories at the same depth cannot
    # both qualify, so the qualifying prefixes form one chain: take its
    # deepest.
    qualifying = [p for p, c in counts.items() if c / n >= share]
    return max(qualifying, key=lambda p: (p.count("/"), p), default="")


def _short_label(rel: str, package_root: str) -> str:
    """A directory label without the scope's package root, and without a
    segment repeated in the next one (``plugins/cgh-x/cgh_x`` reads
    ``plugins/cgh_x``)."""
    if package_root and (rel == package_root or rel.startswith(package_root + "/")):
        rel = rel[len(package_root) :].strip("/")
    parts = [p for p in rel.split("/") if p]
    kept: list[str] = []
    for i, p in enumerate(parts):
        nxt = parts[i + 1] if i + 1 < len(parts) else None
        if nxt is not None and p.replace("-", "_") == nxt.replace("-", "_"):
            continue
        kept.append(p)
    return "/".join(kept)


@dataclass(slots=True)
class _Namer:
    """Names communities from their files, with the scope-wide context a
    good name needs: the package root to leave out of labels, and how many
    files across the scope carry each file-name word (a word spread over
    every subsystem, such as a shared file prefix, does not tell them
    apart)."""

    base: str
    package_root: str
    word_files: Counter[str]

    @classmethod
    def for_groups(cls, groups: list[list[str]]) -> _Namer:
        all_files = [f for g in groups for f in g]
        base = _common_dir(all_files)
        # The package root: a directory holding most of the scope's files is
        # where the code lives, not what tells two subsystems apart.
        package_root = dominant_dir([_rel_dir(f, base) for f in all_files], 0.5)
        word_files: Counter[str] = Counter()
        for f in all_files:
            word_files.update(set(name_tokens(_stem(f))))
        return cls(base, package_root, word_files)

    def label(self, files: list[str]) -> str:
        rel_dirs = [_rel_dir(f, self.base) for f in files]
        top = dominant_dir(rel_dirs)
        root = self.package_root
        if top and not (root and (root == top or root.startswith(top + "/"))):
            return _short_label(top, root) or "root"
        # Spread over several directories (or only the package root holds
        # them): the two directories just under it with the most files.
        anchor = root if top and root else top
        depth = anchor.count("/") + 1 if anchor else 0
        under: Counter[str] = Counter()
        for d in rel_dirs:
            parts = d.split("/") if d else []
            if anchor and not (d == anchor or d.startswith(anchor + "/")):
                under["/".join(parts[:1]) or "root"] += 1
            elif len(parts) > depth:
                under["/".join(parts[: depth + 1])] += 1
            else:
                under[anchor or "root"] += 1
        best = sorted(under, key=lambda d: (-under[d], d))[:2]
        return "+".join(self._display(d) for d in best)

    def _display(self, rel: str) -> str:
        if rel == "root":
            return rel
        if rel == self.package_root:
            return rel.rsplit("/", 1)[-1]
        return _short_label(rel, self.package_root) or "root"

    def name(self, files: list[str]) -> str:
        """``label: word, word``: the directory label, then the file-name
        words that recur in this community and in few others. With no such
        word, the most central file's stem stands in."""
        label = self.label(files)
        skip = set(name_tokens(label.replace("/", " ").replace("+", " ")))
        per_file: Counter[str] = Counter()
        for f in files:
            per_file.update(set(name_tokens(_stem(f))) - skip)

        def score(w: str) -> float:
            # Files carrying the word here, times the squared share of all
            # its files that sit here: a word mostly found in this community
            # outranks a more frequent one found everywhere.
            share = per_file[w] / max(1, self.word_files[w])
            return per_file[w] * share * share

        recurring = sorted(
            (w for w, c in per_file.items() if c >= 2), key=lambda w: (-score(w), w)
        )[:2]
        if recurring:
            return f"{label}: {', '.join(recurring)}"
        stem = _stem(files[0]).strip("_") if files else ""
        return f"{label}: {stem}" if stem and stem not in skip else label


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_communities(
    graph: FileGraph,
    resolution: float = 1.0,
    hub_min_degree: int = HUB_MIN_DEGREE,
) -> CommunityResult:
    """Cluster ``graph`` into named communities, hubs set aside."""
    degree = graph.degree()
    hubs = find_hubs(degree, min_degree=hub_min_degree)
    nodes = [p for p in graph.nodes if p not in hubs]
    index = {p: i for i, p in enumerate(nodes)}
    edges: dict[tuple[int, int], float] = {}
    for (a, b), w in graph.weights.items():
        if a in index and b in index:
            edges[(index[a], index[b])] = w

    labels = louvain(len(nodes), edges, resolution)
    adj: list[dict[int, float]] = [{} for _ in nodes]
    for (i, j), w in edges.items():
        adj[i][j] = adj[i].get(j, 0.0) + w
        adj[j][i] = adj[j].get(i, 0.0) + w
    labels = split_disconnected(labels, adj)
    q = modularity(labels, adj, resolution)

    groups: dict[int, list[int]] = {}
    for i, c in enumerate(labels):
        groups.setdefault(c, []).append(i)

    # Weighted degree inside the group ranks files by centrality.
    def strength_inside(i: int, group: set[int]) -> float:
        return sum(w for j, w in adj[i].items() if j in group)

    ordered: list[tuple[list[str], float, float]] = []
    for members in groups.values():
        member_set = set(members)
        internal = sum(
            w for i in members for j, w in adj[i].items() if j in member_set and i < j
        )
        external = sum(
            w for i in members for j, w in adj[i].items() if j not in member_set
        )
        ranked = sorted(
            members, key=lambda i: (-strength_inside(i, member_set), nodes[i])
        )
        ordered.append(([nodes[i] for i in ranked], internal, external))
    # Biggest first, then the most cohesive, then by first path.
    ordered.sort(key=lambda t: (-len(t[0]), -t[1], t[0][0]))

    namer = _Namer.for_groups([files for files, _i, _e in ordered if len(files) > 1])
    membership: dict[str, int] = {}
    named: list[tuple[str, list[str], float, float]] = []
    seen_names: Counter[str] = Counter()
    for cid, (files, internal, external) in enumerate(ordered, start=1):
        for f in files:
            membership[f] = cid
        name = namer.name(files) if len(files) > 1 else _stem(files[0])
        seen_names[name] += 1
        named.append((name, files, internal, external))

    # Disambiguate repeated names with the central file's stem.
    dupes = {n for n, c in seen_names.items() if c > 1}
    final_names: list[str] = []
    used: set[str] = set()
    for name, files, _i, _e in named:
        if name in dupes:
            name = f"{name} ({_stem(files[0])})"
        while name in used:
            name = f"{name}'"
        used.add(name)
        final_names.append(name)

    links: dict[int, Counter[int]] = {}
    for (a, b), w in graph.weights.items():
        ca, cb = membership.get(a), membership.get(b)
        if ca is None or cb is None or ca == cb:
            continue
        links.setdefault(ca, Counter())[cb] += w
        links.setdefault(cb, Counter())[ca] += w

    symbols_by_comm: dict[int, list[SymbolRef]] = {}
    for s in graph.symbols:
        cid = membership.get(s.file)
        if cid is None:
            continue
        bucket = symbols_by_comm.setdefault(cid, [])
        if len(bucket) < SYMBOLS_CAP:
            bucket.append(s)

    communities = []
    for cid, (name, (_n, files, internal, external)) in enumerate(
        zip(final_names, named, strict=True), start=1
    ):
        lk = links.get(cid, Counter())
        top_links = sorted(lk.items(), key=lambda kv: (-kv[1], kv[0]))
        communities.append(
            Community(
                id=cid,
                name=name,
                files=tuple(files),
                internal_weight=round(internal, 3),
                external_weight=round(external, 3),
                top_symbols=tuple(symbols_by_comm.get(cid, [])),
                links=tuple((c, round(w, 3)) for c, w in top_links),
            )
        )

    hub_rows = []
    for h in hubs:
        touched = set()
        for a, b in graph.weights:
            other = b if a == h else a if b == h else None
            if other is not None and other in membership:
                touched.add(membership[other])
        hub_rows.append(Hub(file=h, degree=degree.get(h, 0), communities=len(touched)))
    hub_rows.sort(key=lambda h: (-h.degree, h.file))

    return CommunityResult(
        resolution=resolution,
        communities=communities,
        hubs=hub_rows,
        membership=membership,
        graph=graph,
        modularity=q,
        files_clustered=len(nodes),
        singletons=sum(1 for c in communities if c.size == 1),
    )


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _common_dir(paths: list[str]) -> str:
    dirs = [os.path.dirname(p) for p in paths]
    if not dirs:
        return ""
    try:
        return os.path.commonpath(dirs)
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# Cache, keyed by index state
# ---------------------------------------------------------------------------

_cache: dict[tuple[str, float, int], CommunityResult] = {}
_cache_lock = threading.Lock()


def reset_for_tests() -> None:
    """Drop every cached clustering."""
    with _cache_lock:
        _cache.clear()


def index_fingerprint(conn: Any, files: list[dict] | None = None) -> str:
    """A digest of the index state clustering depends on: every File row's
    path, mtime and blob, plus the IMPORTS and CALLS edge counts. A reindex
    of any file, an added or removed file, or a re-resolved call set changes
    it."""
    if files is None:
        files = _file_rows(conn)
    h = hashlib.sha256()
    for r in sorted(files, key=lambda r: r["path"]):
        h.update(
            f"{r['path']}\0{r.get('mtime')}\0{r.get('git_blob_sha')}\0"
            f"{r.get('role')}\n".encode("utf-8", "surrogateescape")
        )
    h.update(f"{conn.count_edges('IMPORTS')}:{conn.count_edges('CALLS')}".encode())
    return h.hexdigest()


def communities_for(
    conn: Any,
    resolution: float = 1.0,
    hub_min_degree: int = HUB_MIN_DEGREE,
) -> CommunityResult:
    """Communities of the graph behind ``conn``, from the cache when the
    index has not changed since the last run with the same settings."""
    files = _file_rows(conn)
    key = (index_fingerprint(conn, files), float(resolution), int(hub_min_degree))
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    result = detect_communities(
        build_file_graph(conn, files), resolution, hub_min_degree
    )
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
    return result


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def community_rows(
    result: CommunityResult,
    short: Callable[[str], str],
    min_size: int = 2,
    max_communities: int = 30,
) -> tuple[list[dict], bool]:
    """JSON rows for the communities of one scope, smallest dropped and
    capped. Returns (rows, truncated)."""
    kept = [c for c in result.communities if c.size >= max(1, min_size)]
    truncated = len(kept) > max_communities
    kept = kept[: max(0, max_communities)]
    names = {c.id: c.name for c in result.communities}
    rows = []
    for c in kept:
        dirs = Counter(os.path.dirname(short(f)) or "." for f in c.files)
        row: dict[str, Any] = {
            "id": c.id,
            "name": c.name,
            "size": c.size,
            "central": short(c.files[0]),
            "dirs": [
                {"dir": d, "files": n}
                for d, n in sorted(dirs.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            ],
            "files": [short(f) for f in c.files[:FILES_CAP]],
            "top_symbols": [
                {
                    "name": s.name,
                    "file": short(s.file),
                    "line": s.line,
                    "callers": s.callers,
                }
                for s in c.top_symbols
            ],
            "cohesion": c.cohesion,
            "links": [
                {"id": other, "name": names.get(other, "?"), "weight": w}
                for other, w in c.links[:LINKS_CAP]
            ],
        }
        if c.size > FILES_CAP:
            row["files_more"] = c.size - FILES_CAP
        rows.append(row)
    return rows, truncated


def subsystem_hint(
    result: CommunityResult,
    seed_files: list[str],
    exclude: Iterable[str] = (),
    max_files: int = 5,
) -> Community | None:
    """The community most of ``seed_files`` belong to, earlier seeds
    weighing more (they are the better matches), and its central files not
    already in ``exclude``. None when no seed falls in a community of two
    files or more."""
    votes: Counter[int] = Counter()
    by_id = {c.id: c for c in result.communities}
    for rank, f in enumerate(seed_files[:5]):
        cid = result.membership.get(f)
        if cid is not None and by_id[cid].size > 1:
            votes[cid] += 5 - rank
    if not votes:
        return None
    best = min(votes, key=lambda c: (-votes[c], c))
    c = by_id[best]
    skip = set(exclude)
    files = tuple(f for f in c.files if f not in skip)[:max_files]
    return Community(
        id=c.id,
        name=c.name,
        files=files,
        internal_weight=c.internal_weight,
        external_weight=c.external_weight,
        top_symbols=c.top_symbols,
        links=c.links,
    )


def hub_rows(result: CommunityResult, short: Callable[[str], str]) -> list[dict]:
    return [
        {"file": short(h.file), "degree": h.degree, "communities": h.communities}
        for h in result.hubs[:HUBS_CAP]
    ]


def communities_payload(
    per_scope: Iterable[tuple[str, CommunityResult]],
    short: Callable[[str], str],
    resolution: float = 1.0,
    min_size: int = 2,
    max_communities: int = 30,
) -> dict:
    """The communities tool payload over every scope. Rows carry ``scope``;
    ids are per scope. ``max_communities`` caps each scope."""
    communities: list[dict] = []
    utilities: list[dict] = []
    scopes: list[dict] = []
    truncated = False
    for scope, result in per_scope:
        rows, cut = community_rows(result, short, min_size, max_communities)
        truncated = truncated or cut
        communities.extend({**r, "scope": scope} for r in rows)
        utilities.extend({**h, "scope": scope} for h in hub_rows(result, short))
        scopes.append(
            {
                "scope": scope,
                "files_clustered": result.files_clustered,
                "edges": len(result.graph.weights),
                "communities": len(result.communities),
                "shown": len(rows),
                "singletons": result.singletons,
                "shared_utilities": len(result.hubs),
                "modularity": result.modularity,
            }
        )
    return {
        "resolution": resolution,
        "min_size": min_size,
        "count": len(communities),
        "truncated": truncated,
        "communities": communities,
        "shared_utilities": utilities,
        "scopes": scopes,
        "note": (
            "file-level graph of IMPORTS plus CALLS (name-matched, best effort); "
            "test files and shared utilities are left out of clustering. "
            "Each scope is clustered on its own graph."
        ),
    }
