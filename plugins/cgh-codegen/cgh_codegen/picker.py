# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-14
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Reference selection. Given the file to generate, find the best
#              existing file to mirror. Two signals combined: the graph (a
#              file defining a symbol related to the target's name, via the
#              public find_symbol_files) and the filesystem (a sibling in the
#              same directory of the same kind). Degrades to filesystem-only
#              when the graph is unavailable. All candidate paths are confined
#              to the repo root.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class CodegenError(RuntimeError):
    """Base error for the cgh-codegen plugin."""


# Name-stem prefixes/suffixes that carry no pattern signal on their own.
_NOISE_TOKENS = frozenset({"test", "tests", "spec", "specs", "impl", "base", "mod"})

# How long reference selection waits on the graph before giving up and using
# filesystem siblings only. Reference selection is normally sub-second; this is
# a generous ceiling that turns an indefinite hang behind a wedged owner into a
# bounded, fail-fast degradation.
_GRAPH_DEADLINE_S = 10.0


@dataclass(slots=True)
class Candidate:
    path: Path
    score: int
    reasons: list[str] = field(default_factory=list)


def _confine(root: Path, candidate: str) -> Path | None:
    """Resolve ``candidate`` under ``root``; None if it escapes the root."""
    p = Path(candidate)
    resolved = (p if p.is_absolute() else root / p).resolve()
    if resolved == root or root in resolved.parents:
        return resolved
    return None


def _split_words(stem: str) -> list[str]:
    """Break a file stem into lowercase words across snake_case and
    camelCase/PascalCase, e.g. test_userService -> [test, user, service]."""
    parts: list[str] = []
    for chunk in re.split(r"[_\-.]+", stem):
        parts.extend(re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", chunk))
    return [p.lower() for p in parts if p]


def name_tokens(stem: str) -> list[str]:
    """Case variants a symbol search should try for this stem. Drops the
    noise words (test, spec, ...) so `test_user_service` searches for the
    thing under test, then offers snake, Pascal and per-word variants since
    the graph's substring match is case-sensitive."""
    words = _split_words(stem)
    meaningful = [w for w in words if w not in _NOISE_TOKENS] or words
    tokens: list[str] = []

    def add(t: str) -> None:
        if t and t not in tokens:
            tokens.append(t)

    add("_".join(meaningful))  # snake_case
    add("".join(w.capitalize() for w in meaningful))  # PascalCase
    for w in meaningful:
        add(w)
        add(w.capitalize())
    return tokens


def _graph_candidates_query(
    root: Path, tokens: list[str]
) -> tuple[set[Path], bool | None]:
    """Files the graph says define a symbol matching any token. The bool is
    graph availability: True if a query succeeded, False if the graph could
    not be read at all, None if there was nothing to ask."""
    from codegraph.plugin_api import find_symbol_files

    files: set[Path] = set()
    available: bool | None = None
    for tok in tokens:
        rows = find_symbol_files(str(root), tok)
        if rows is None:
            if available is None:
                available = False
            continue
        available = True
        for r in rows:
            confined = _confine(root, r["file"])
            if confined is not None:
                files.add(confined)
    return files, available


def _graph_candidates(
    root: Path, tokens: list[str], timeout: float = _GRAPH_DEADLINE_S
) -> tuple[set[Path], bool | None]:
    """Bounded wrapper around the graph query. find_symbol_files talks to the
    repo's owner, and a wedged owner (one whose reindex is stuck holding the
    write lock) can block that read indefinitely, which is how a codegen call
    hung for half an hour with no output. Run it under a deadline: if it does
    not answer in time, treat the graph as unavailable and let selection fall
    back to filesystem siblings, so codegen degrades instead of hanging."""
    import threading

    box: dict = {"result": (set(), None)}

    def _run() -> None:
        box["result"] = _graph_candidates_query(root, tokens)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        # The graph read is stuck. Don't wait it out; the daemon thread unwinds
        # on its own if the query ever returns. Graph unavailable, siblings only.
        return set(), False
    return box["result"]


def pick_reference(
    repo_root: str | Path,
    target: str,
    explicit_reference: str | None = None,
    graph_timeout: float | None = None,
) -> dict:
    """Choose the file to mirror when generating ``target``.

    Returns ``{reference, reason, candidates, graph_available}``.
    ``reference`` is a repo-relative path or None when nothing fits (the
    caller then requires an explicit reference). An explicit reference is
    validated and returned as-is.
    """
    root = Path(repo_root).resolve()

    if explicit_reference:
        ref = _confine(root, explicit_reference)
        if ref is None:
            raise CodegenError(f"reference {explicit_reference!r} is outside the repo")
        if not ref.is_file():
            raise CodegenError(f"reference {explicit_reference!r} is not a file")
        return {
            "reference": _rel(root, ref),
            "reason": "given by the caller",
            "candidates": [],
            "graph_available": None,
        }

    tgt = _confine(root, target)
    if tgt is None:
        raise CodegenError(f"target {target!r} is outside the repo")

    suffix = tgt.suffix
    parent = tgt.parent
    tokens = name_tokens(tgt.stem)
    graph_files, graph_available = _graph_candidates(
        root, tokens, graph_timeout if graph_timeout is not None else _GRAPH_DEADLINE_S
    )
    tgt_words = set(_split_words(tgt.stem))

    # Candidate pool: existing files of the same kind, from the target's
    # directory and from the graph hits. The target itself never counts.
    pool: set[Path] = set()
    if parent.exists():
        pool.update(
            p.resolve()
            for p in parent.glob(f"*{suffix}")
            if p.is_file() and p.resolve() != tgt
        )
    pool.update(f for f in graph_files if f.suffix == suffix and f != tgt)

    # A file in the target's own directory is the same kind of thing in the
    # same place (a new commands_*.py mirrors its commands_*.py neighbours,
    # not a test that merely mentions the name), so the sibling weight is set
    # above the graph-match weight: a same-directory sibling outranks a bare
    # cross-directory symbol match, while a file that is BOTH still wins.
    _GRAPH_WEIGHT = 3
    _SIBLING_WEIGHT = 4
    # Each shared name word weighs more than a bare graph symbol match, so
    # among same-directory siblings the closest NAME wins: for a test the file
    # name is the signal (test_dedup_family for a family-dedup test), and a
    # graph hit on a differently-named sibling should not outrank it. Two
    # shared words (2 x 2 = 4) clear the graph weight (3); one word does not.
    _OVERLAP_WEIGHT = 2
    scored: list[Candidate] = []
    for path in pool:
        c = Candidate(path=path, score=0)
        if path in graph_files:
            c.score += _GRAPH_WEIGHT
            c.reasons.append("defines a matching symbol")
        if path.parent == parent:
            c.score += _SIBLING_WEIGHT
            c.reasons.append("sibling in the same directory")
        overlap = len(tgt_words & set(_split_words(path.stem)))
        if overlap:
            c.score += overlap * _OVERLAP_WEIGHT
            c.reasons.append(f"shares {overlap} name word(s)")
        if c.score:
            scored.append(c)

    scored.sort(key=lambda c: (c.score, str(c.path)), reverse=True)

    if not scored:
        return {
            "reference": None,
            "reason": (
                "no analogue found in the graph or the target directory; "
                "pass an explicit reference"
            ),
            "candidates": [],
            "graph_available": graph_available,
        }

    best = scored[0]
    return {
        "reference": _rel(root, best.path),
        "reason": ", ".join(best.reasons),
        "candidates": [_rel(root, c.path) for c in scored[:5]],
        "graph_available": graph_available,
    }


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
