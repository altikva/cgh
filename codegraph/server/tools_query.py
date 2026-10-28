# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-04-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: MCP query tools: symbol_lookup, callers, callees, imports_of,
#              indexed_files, search_symbols, subgraph, rationale.

from __future__ import annotations

import os
import re
from pathlib import Path

from codegraph.core.utils import short_path as _short_path
from codegraph.server._budget import dumps_answer

# Forward call-chain traversal bounds for find_callees(max_depth>1): the
# hop ceiling, the per-node edge fan-out, and the total callees returned.
# Mirror the reverse-reach caps impact_of already uses.
_CALLEE_DEPTH_CAP = 5
_CALLEE_FANOUT_CAP = 500
_CALLEE_TOTAL_CAP = 300


def _dirs_text(dirs: list[dict]) -> str:
    return ", ".join(f"{d['glob']} ({d['hits']})" for d in dirs)


def _fit_extra(extra: dict, max_bytes: int) -> None:
    """Shrink a sentence's own fields to about ``max_bytes``: the docstring
    heads go first, from the last symbol up, then the last symbols, doc
    sections and synonyms (a few of each stay). ``symbols_more`` counts the
    symbols left out."""
    from codegraph.server._budget import answer_bytes

    symbols = extra.get("symbols") or []
    for row in reversed(symbols):
        if answer_bytes(extra) <= max_bytes:
            return
        row.pop("doc", None)
    for key, keep in (("symbols", 3), ("docs", 1), ("expanded", 3)):
        rows = extra.get(key) or []
        while len(rows) > keep and answer_bytes(extra) > max_bytes:
            rows.pop()
            if key == "symbols":
                # Counted as it goes, so the count is part of the size.
                extra["symbols_more"] = extra.get("symbols_more", 0) + 1


def _counts_payload(
    hits: list[dict],
    with_scope: bool,
    roots: list | None,
    limit: int,
    reserve: int = 0,
) -> dict:
    """count_only: hits per file, the busiest first, without the lines, and
    the directories holding most of them. ``reserve`` is room kept for the
    keys the tool adds."""
    from collections import Counter

    from codegraph.analysis.pattern import PATTERN_ANSWER_BYTES
    from codegraph.analysis.query_intent import top_dirs
    from codegraph.server._budget import cap_answer

    counts = Counter((h.get("scope", "parent"), h["file"]) for h in hits)
    files = []
    for (scope, file), n in counts.most_common():
        row: dict = {"scope": scope} if with_scope else {}
        row.update({"file": file, "count": n})
        files.append(row)
    out: dict = {"total": len(hits), "count_only": True, "files": files}
    dirs = top_dirs([h["file"] for h in hits], roots)
    if dirs:
        out["top_dirs"] = dirs
    if len(hits) >= limit:
        out["limit_reached"] = True
    cap_answer(
        out,
        ["files"],
        "File list cut to fit: pass one of `top_dirs` as glob.",
        max_bytes=PATTERN_ANSWER_BYTES - 400 - reserve,
    )
    return out


def _hits_payload(
    hits: list[dict],
    pattern: str = "",
    regex: bool = True,
    case_sensitive: bool = False,
    with_scope: bool = False,
    roots: list | None = None,
    count_only: bool = False,
    reserve: int = 0,
    limit: int = 0,
) -> dict:
    """total, the hits grouped by file under `files` and, when the list was
    cut to fit the answer budget, `truncated`, where the rest is (`more` maps
    a file to its hits not shown, for the first few files), `top_dirs` (the
    directories holding most hits, as globs) and a hint naming them.
    ``count_only`` answers hits per file instead of the lines. ``reserve``
    is room kept for keys the tool adds next to these."""
    from codegraph.analysis.pattern import (
        HITS_BUDGET_BYTES,
        MORE_FILES_SHOWN,
        PATTERN_ANSWER_BYTES,
        group_hits,
        match_finder,
    )
    from codegraph.analysis.query_intent import top_dirs
    from codegraph.server._budget import answer_bytes

    if count_only:
        return _counts_payload(hits, with_scope, roots, limit or len(hits) + 1, reserve)
    find = match_finder(pattern, regex, case_sensitive) if pattern else None
    budget = HITS_BUDGET_BYTES - reserve
    dirs: list[dict] | None = None
    # The hit budget leaves out `more` and the hint: when they push the
    # answer past its ceiling, take the excess off the hits and group again.
    for _ in range(4):
        groups, shown, more = group_hits(hits, find, budget, with_scope)
        out: dict = {"total": len(hits), "files": groups}
        if not more:
            break
        out["shown"] = shown
        out["truncated"] = True
        out["more"] = dict(list(more.items())[:MORE_FILES_SHOWN])
        if len(more) > MORE_FILES_SHOWN:
            out["more_files"] = len(more)
        if dirs is None:
            dirs = top_dirs([h["file"] for h in hits], roots)
        if dirs:
            out["top_dirs"] = dirs
            out["hint"] = (
                f"{len(hits) - shown} more hit(s) not shown, counted per file in "
                f"`more`. Most hits are under {_dirs_text(dirs)}. Narrow the "
                "pattern, pass one of these as `glob`, or use count_only=True."
            )
        else:
            out["hint"] = (
                f"{len(hits) - shown} more hit(s) not shown, counted per file in "
                '`more`. Narrow the pattern, or pass a glob such as "src/**/*.py".'
            )
        # About 300 bytes, plus the pattern, for the keys the tool adds
        # (pattern, glob, backend, confidence, next).
        excess = (
            answer_bytes(out) + 300 + reserve + 2 * len(pattern) - PATTERN_ANSWER_BYTES
        )
        if excess <= 0 or shown <= 1:
            break
        # A small excess may not free a whole hit: take at least a line off.
        budget -= max(excess, 100)
    return out


def _shown_scopes(payload: dict, scopes: dict) -> dict:
    """The commit info of the parent and of each scope the answer shows a
    hit, symbol or doc from: a federation of many children would otherwise
    spend the answer's budget on scopes it says nothing about."""
    seen = {"parent"}
    for key in ("files", "symbols", "docs"):
        for row in payload.get(key) or []:
            if isinstance(row, dict) and row.get("scope"):
                seen.add(row["scope"])
    return {k: v for k, v in scopes.items() if k in seen}


def _fit_pattern_answer(build) -> str:
    """The pattern_search answer as returned: at most PATTERN_ANSWER_BYTES
    measured on the very text the agent receives (``wire_bytes``), not on an
    estimate of the keys added around the hits.

    ``build(slack)`` makes the whole payload with ``slack`` bytes taken off
    the hits. Each pass measures the serialized answer and adds what it is
    over to the slack, so the hits shrink and `more`, `top_dirs` and the
    hint stay right. When the hits are down to the first one and the answer
    is still over (a long pattern, a sentence's fields), those fields and
    then the longest strings are shortened."""
    from codegraph.analysis.pattern import PATTERN_ANSWER_BYTES
    from codegraph.server._budget import (
        answer_bytes,
        clip_strings,
        dumps_answer,
        wire_bytes,
    )

    slack = 0
    payload: dict = {}
    for _ in range(8):
        payload = build(slack)
        text = dumps_answer(payload)
        excess = wire_bytes(text) - PATTERN_ANSWER_BYTES
        if excess <= 0:
            return text
        slack += excess

    def over() -> int:
        return wire_bytes(dumps_answer(payload)) - PATTERN_ANSWER_BYTES

    if over() > 0:
        _fit_extra(payload, answer_bytes(payload) - over())
    more = payload.get("more") or {}
    if over() > 0 and len(more) > 1:
        # Long paths: fewer files named in `more`, `more_files` counting all.
        payload.setdefault("more_files", len(more))
        while len(more) > 1 and over() > 0:
            more.popitem()
    for _ in range(4):
        if over() <= 0:
            break
        # Escaping makes the wire form longer than the raw one: aim lower.
        clip_strings(payload, answer_bytes(payload) - 2 * over())
    return dumps_answer(payload)


# file_at_ref with no range: a file up to this size comes back whole, a
# larger one as an outline of at most _AT_REF_OUTLINE_BYTES, or its first
# _AT_REF_HEAD_LINES lines when cgh does not parse its language.
_AT_REF_WHOLE_BYTES = 4_000
_AT_REF_OUTLINE_BYTES = 4_000
_AT_REF_HEAD_LINES = 30


def _symbol_range(
    rel: str, text: str, symbol: str, start_line: int, end_line: int
) -> tuple[int, int, dict]:
    """(start, end, keys to add) of ``symbol`` in the file. A start_line
    inside the symbol goes on from there (the next page of a long one); an
    end_line inside it stops there."""
    from codegraph.analysis.refs import RefError, find_symbol, outline_of

    outline = outline_of(rel, text)
    if outline is None:
        raise RefError(
            f"cgh cannot parse {rel} for symbols: pass start_line / end_line"
        )
    found = find_symbol(outline, symbol)
    if not found:
        near = [e["name"] for e in outline if symbol.lower() in e["name"].lower()]
        tip = f" Close names: {', '.join(near[:5])}." if near else ""
        raise RefError(
            f"no symbol {symbol!r} in {rel}.{tip} The outline holds top-level "
            "definitions and methods only: for a nested one, find its line "
            "with pattern_search(ref=...) and pass start_line / end_line."
        )
    hit = found[0]
    start, end = hit["start_line"], hit["end_line"]
    if start < start_line <= end:
        start = start_line
    if start <= end_line < end:
        end = end_line
    extra: dict = {"symbol": hit["name"], "kind": hit["kind"]}
    if len(found) > 1:
        extra["other_matches"] = [
            {"name": e["name"], "lines": f"{e['start_line']}-{e['end_line']}"}
            for e in found[1:6]
        ]
    return start, end, extra


def _outline_answer(head: dict, rel: str, text: str) -> str:
    """The answer for a large file read with no range: its outline, or its
    first lines when there is none, and how to ask for a part."""
    from codegraph.analysis.refs import outline_of, slice_lines
    from codegraph.server._budget import cap_answer, dumps_answer

    total = len(text.splitlines())
    size = len(text.encode("utf-8"))
    payload: dict = {**head, "path": rel, "total_lines": total, "bytes": size}
    outline = outline_of(rel, text)
    ask = "call again with start_line / end_line, or symbol=<name> for one definition."
    if outline:
        payload["outline"] = [
            {"name": e["name"], "kind": e["kind"], "lines": _lines(e)} for e in outline
        ]
        payload["hint"] = f"File of {size} bytes, outline only: {ask}"
        cut = f"{payload['hint']} Outline cut to fit: more entries follow the last one."
        cap_answer(payload, ["outline"], cut, max_bytes=_AT_REF_OUTLINE_BYTES)
    else:
        part = slice_lines(rel, text, 1, _AT_REF_HEAD_LINES)
        payload["head"] = part["lines"]
        payload["hint"] = (
            f"File of {size} bytes with no outline (language not parsed or no "
            f"definitions), first {len(part['lines'])} lines only: {ask}"
        )
        cut = f"{payload['hint']} Head cut to fit."
        cap_answer(payload, ["head"], cut, max_bytes=_AT_REF_OUTLINE_BYTES)
    return dumps_answer(payload)


def _lines(entry: dict) -> str:
    return f"{entry['start_line']}-{entry['end_line']}"


def register(mcp) -> None:
    """Register query tools on the given FastMCP instance."""
    import codegraph.server as _srv
    from codegraph.analysis.federation import (
        child_fts_symbol_lookup,
        child_fts_symbol_search,
        federate_flat,
    )
    from codegraph.analysis.terraform import lookup as tf_lookup
    from codegraph.analysis.terraform import search as tf_search
    from codegraph.analysis.terraform import used_by as tf_used_by
    from codegraph.server import _get_conn, _get_fts, _logged_tool

    def _federate(query_fn):
        """Parent + federated children fan-out, flattened. Returns
        (results_with_scope, warnings). See federation.federate_flat."""
        return federate_flat(_get_conn, _srv._root, query_fn)

    def _search_repo_at_ref(
        pattern, glob, max_results, regex, case_sensitive, repo, ref, count_only=False
    ) -> str:
        """pattern_search on one repo (this one or a declared sibling): its
        tree at ``ref`` read from git, or its working tree when no ref."""
        from codegraph.analysis.pattern import pattern_search as _search
        from codegraph.analysis.refs import (
            RefError,
            grep_at_ref,
            resolve_repo,
            resolve_target,
        )

        try:
            if ref:
                target = resolve_target(_srv._root, repo, ref)
                name, sha = target.name, target.sha
                hits = grep_at_ref(
                    target, pattern, glob, max_results, regex, case_sensitive
                )
                backend = "git-grep-ref"
            else:
                name, top = resolve_repo(_srv._root, repo)
                sha = None
                hits, backend = _search(
                    top,
                    pattern=pattern,
                    glob=glob,
                    max_results=max_results,
                    regex=regex,
                    case_sensitive=case_sensitive,
                    include_extra_dirs=False,
                )
        except RefError as exc:
            return dumps_answer({"pattern": pattern, "error": str(exc)})
        rows = [{"file": h.file, "line": h.line, "text": h.text} for h in hits]

        def build(slack: int) -> dict:
            payload = {
                "pattern": pattern,
                "glob": glob or None,
                "repo": name or None,
                "ref": ref or None,
                "commit": sha,
                "backend": backend,
                **_hits_payload(
                    rows,
                    pattern,
                    regex,
                    case_sensitive,
                    roots=None if ref else [top],
                    count_only=count_only,
                    reserve=slack,
                    limit=max_results,
                ),
            }
            payload.update(_grep_verdict(payload, pattern, glob, regex))
            return payload

        return _fit_pattern_answer(build)

    def _central() -> dict[str, int]:
        """Import in-degree per file of the parent graph (hotspots' term)."""
        from codegraph.analysis.query_intent import importers

        try:
            return importers(_get_conn(), _srv._root)
        except Exception:  # no graph yet: rank on recency alone
            return {}

    def _fts_scoped(text: str, limit: int, prose: bool = True) -> list[tuple]:
        """[(scope, FTSResult)] from the FTS of the parent and each child,
        scanner findings left out (they are not definitions)."""
        from codegraph.analysis.federation import for_each_child_fts
        from codegraph.core.fts import fts_search as _fts

        out: list[tuple] = []
        if not text.strip():
            return out
        try:
            out += [
                ("parent", r) for r in _fts(_get_fts(), text, limit=limit, prose=prose)
            ]
        except Exception:  # an FTS that cannot answer adds nothing
            pass
        if _srv._root is not None:
            for scoped in for_each_child_fts(
                _srv._root, lambda c, _r: _fts(c, text, limit=limit, prose=prose)
            ):
                if not scoped.error:
                    out += [(scoped.scope, r) for r in scoped.payload or []]
        return [(s, r) for s, r in out if r.kind != "finding"]

    def _near_names(name: str, limit: int = 3) -> list[str]:
        """Symbol names close to ``name`` (fused BM25 and trigram FTS), for
        a lookup that found nothing."""
        names: list[str] = []
        for _scope, r in _fts_scoped(name, limit * 4, prose=False):
            if r.kind == "md_section" or r.name == name or r.name in names:
                continue
            names.append(r.name)
            if len(names) >= limit:
                break
        return names

    def _understand(intent, limit: int = 8) -> tuple[list[dict], list[tuple]]:
        """For a sentence: the definitions its identifiers name exactly, and
        the FTS hits on its words and their synonyms as (coverage, scope,
        FTSResult), tests last, then the hits touching the most ideas of
        the question, then the FTS order."""
        from codegraph.analysis.query_intent import coverage, is_test_path

        exact: list[dict] = []
        for ident in intent.identifiers[:4]:
            names = [ident]
            if "." in ident:
                names.append(ident.rsplit(".", 1)[-1])
            for n in names:
                rows, _warnings = _federate(_definitions_query(n))
                if rows:
                    exact.extend(rows)
                    break
        hits = _fts_scoped(intent.fts_text, max(limit * 3, 24))
        scored = [
            (coverage(intent, r.name, r.docstring), i, scope, r)
            for i, (scope, r) in enumerate(hits)
        ]
        scored.sort(key=lambda t: (is_test_path(t[3].file_path), -t[0], t[1]))
        return exact, [(c, s, r) for c, _i, s, r in scored]

    def _sentence_verdict(intent, exact: int, scored: list[tuple]) -> dict:
        """confidence, and why / next when it is low, for a sentence."""
        from codegraph.analysis.query_intent import call, sentence_confidence

        best = scored[0][0] if scored else 0
        conf = sentence_confidence(intent, exact, best, len(scored))
        out: dict = {"confidence": conf}
        if conf != "low":
            return out
        words = intent.interpreted_as
        if not words:
            out["why"] = "Only stopwords in the question: nothing left to search for."
            out["next"] = "Name the function, class or file you are after."
            return out
        if scored:
            out["why"] = (
                "Only weak matches: no result touches more than one idea of "
                f"the question ({', '.join(words)})."
            )
        else:
            out["why"] = (
                f"No symbol name, docstring or doc section matches {', '.join(words)}, "
                "nor their synonyms."
            )
        longest = max(words, key=len)
        out["next"] = call("pattern_search", longest, regex=False)
        return out

    def _grep_verdict(payload: dict, pattern: str, glob: str, regex: bool) -> dict:
        """confidence, and why / next, for a literal or regex search."""
        from codegraph.analysis.query_intent import call

        if payload.get("count_only"):
            return {"confidence": "high"}
        if payload.get("truncated"):
            out: dict = {"confidence": "medium"}
            dirs = payload.get("top_dirs") or []
            if dirs:
                out["next"] = call("pattern_search", pattern, glob=dirs[0]["glob"])
            return out
        if payload.get("total"):
            return {"confidence": "high"}
        kind = "regex" if regex else "literal"
        where = f" in {glob}" if glob else ""
        out = {
            "confidence": "low",
            "why": f"No line matches the {kind} {pattern!r}{where}.",
        }
        if glob:
            out["next"] = call("pattern_search", pattern)
        elif re.fullmatch(r"[A-Za-z_][\w.]*", pattern or ""):
            near = _near_names(pattern)
            out["next"] = (
                call("symbol_lookup", near[0]) if near else call("fts_search", pattern)
            )
            if near:
                out["candidates"] = near
        elif regex and re.search(r"[\\^$*+\[\]{}|()]", pattern or ""):
            out["next"] = call("pattern_search", pattern, regex=False)
        else:
            out["next"] = call("fts_search", pattern)
        return out

    @mcp.tool()
    @_logged_tool
    def file_at_ref(
        path: str,
        ref: str,
        repo: str = "",
        start_line: int = 0,
        end_line: int = 0,
        symbol: str = "",
    ) -> str:
        """
        Read a file as it is at a branch, tag or commit, straight from git:
        no checkout, no index. Use it for code on another branch of this
        repo, or in a sibling repo declared under [codegraph] siblings of
        ~/.codegraph/config.toml (e.g. the API's origin/develop seen from a frontend worktree),
        instead of `git show` in a shell.

        Args:
          path:       path inside the repo, as pattern_search(ref=...) returns
          ref:        branch, tag, remote-tracking branch or commit id
          repo:       a declared sibling repo (directory name); "" = this repo
          start_line: first line, 1-based (0 = not set)
          end_line:   last line, inclusive; 0 = to the end. At most 400
                      lines per call (`truncated` says when it stopped)
          symbol:     a function, class or method (Class.method) defined in
                      the file at that ref: answers its lines only

        With no range and no symbol, a file up to about 4 KB comes back
        whole. A larger one comes back as an `outline`: its symbols with
        their line ranges, parsed from the file at that ref ({name, kind,
        start_line, end_line}), or `head`, its first lines, for a language
        cgh does not parse; then call again with start_line / end_line or
        symbol.

        Returns {repo, ref, commit, path, total_lines, start_line, end_line,
        truncated, lines: [{line, text}]}, or {error} for an unknown repo,
        ref, path, symbol or a bad range. The answer also stops at about
        10 KB: then `truncated` is true, end_line is the last line shown
        and `next_start_line` says where to go on. Never fetches: run
        `git fetch` there for a fresh remote branch.
        """
        from codegraph.analysis.refs import (
            RefError,
            blob_at_ref,
            resolve_target,
            slice_lines,
        )
        from codegraph.server._budget import cap_answer, dumps_answer

        try:
            target = resolve_target(_srv._root, repo, ref)
            rel, text = blob_at_ref(target, path)
            head = {"repo": target.name or None, "ref": ref, "commit": target.sha}
            if symbol:
                start_line, end_line, extra = _symbol_range(
                    rel, text, symbol, start_line, end_line
                )
                head.update(extra)
            elif not start_line and not end_line:
                if len(text.encode("utf-8")) > _AT_REF_WHOLE_BYTES:
                    return _outline_answer(head, rel, text)
            out = slice_lines(rel, text, start_line, end_line)
        except RefError as exc:
            return dumps_answer({"path": path, "ref": ref, "error": str(exc)})
        payload = {**head, **out}
        if cap_answer(
            payload,
            ["lines"],
            "Lines cut to fit the answer: call again with start_line = "
            "next_start_line, or ask for a narrower line range.",
        ):
            payload.pop("dropped", None)
            if payload["lines"]:
                payload["end_line"] = payload["lines"][-1]["line"]
            payload["next_start_line"] = payload["end_line"] + 1
        elif payload.get("truncated"):
            payload["next_start_line"] = payload["end_line"] + 1
        return dumps_answer(payload)

    def _retry_children_via_fts(results, warnings, retry, to_row):
        """Re-answer children whose graph DB was unreachable from their FTS.

        ``retry(scopes)`` runs the FTS query for the locked scopes and
        ``to_row(hit, scope)`` shapes each hit like the tool's own rows.
        Returns the results with the recovered rows appended, and the warnings
        trimmed to the scopes that no backend could answer. The parent's own
        warning, if any, is left untouched.
        """
        locked = {
            w["scope"] for w in warnings if w.get("scope") and w["scope"] != "parent"
        }
        if not locked or _srv._root is None:
            return results, warnings
        buckets, failures = retry(locked)
        for scope, hits in buckets:
            results.extend(to_row(hit, scope) for hit in hits)
        kept = [w for w in warnings if w.get("scope") not in locked]
        kept.extend({"scope": scope, "error": error} for scope, error in failures)
        return results, kept

    @mcp.tool()
    @_logged_tool
    def pattern_search(
        pattern: str,
        glob: str = "",
        max_results: int = 50,
        regex: bool = True,
        case_sensitive: bool = False,
        repo: str = "",
        ref: str = "",
        count_only: bool = False,
        format: str = "",
    ) -> str:
        """
        Regex / substring pattern search across the indexed repo. Use this
        INSTEAD of the Grep tool for any "find all occurrences of X"
        question, returns hits grouped by file so you can then Read the
        exact line ranges that matter (never whole files).

        Returns {pattern, glob, backend, total, files: [{file, hits:
        [[line, text], ...]}], confidence}. Each line is clipped to about
        160 characters around the match, an ellipsis marking a cut side.
        Files holding a definition come first, then the most imported and
        the most recently changed ones.

        Respects .gitignore (via ripgrep / git-grep). Automatically scans
        extra_dirs configured on this project. Caps output at max_results,
        and at about 2.5 KB: past that, the answer has `truncated: true`,
        `shown`, `more` counting the hits left out per file, and
        `top_dirs`, the directories holding most hits as globs with their
        counts, to pass back as `glob`. `count_only=True` answers hits per
        file ({file, count}, busiest first) without the lines.

        A sentence ("where are webhooks retried") is understood rather
        than grepped: the identifiers it names are searched, its words and
        their French / English synonyms go through the symbol and doc
        FTS, and the answer adds `interpreted_as`, `expanded`, `symbols`
        and `docs` (`symbols_more` counts the symbols cut to fit the
        answer). `confidence` is high, medium or low; a low or empty
        answer says `why` and gives `next`, a call to try instead.

        Args:
          pattern:        Python-style regex on every backend (\\w \\d \\s \\b, lazy quantifiers, classes), literal when regex=False
          glob:           optional shell glob, e.g. "*.py", "api/handlers/*"
          max_results:    hard cap (default 50)
          regex:          treat pattern as regex (default True)
          case_sensitive: default False
          repo:           a sibling repo declared under [codegraph] siblings
                          of ~/.codegraph/config.toml (by directory name),
                          instead of this project
          ref:            search the tree at this branch, tag or commit
                          (e.g. "origin/develop") straight from git, no
                          checkout needed; hit files are paths inside the
                          repo, read them with file_at_ref
          format:         "text" answers a grep-like layout instead of
                          JSON (default: the [mcp] answer_format setting)

        Example: pattern_search(r"@router\\.(get|post)", glob="*.py")
                 → list of route declarations with line numbers.
        Example: pattern_search("class Association", repo="my-api",
                                ref="origin/develop")

        Federated: also scans federated subrepo trees. When some hits come
        from a federated child, each file group is tagged with `scope`
        (parent / <subrepo-name>); otherwise there is no `scope`. With repo
        or ref set, only that one repo is searched.
        """
        from codegraph.analysis.federation import resolve_children
        from codegraph.analysis.pattern import (
            COUNT_ONLY_MAX_RESULTS,
            PATTERN_ANSWER_BYTES,
            SENTENCE_EXTRA_BYTES,
        )
        from codegraph.analysis.pattern import pattern_search as _search
        from codegraph.analysis.query_intent import (
            interpret,
            plain_words,
            rank_hits,
        )
        from codegraph.server._budget import answer_bytes

        if count_only:
            max_results = max(max_results, COUNT_ONLY_MAX_RESULTS)
        if repo or ref:
            return _search_repo_at_ref(
                pattern, glob, max_results, regex, case_sensitive, repo, ref, count_only
            )

        # Resolve children once and reuse for the cap below (this used to
        # read + parse config.toml twice per query).
        children = resolve_children(_srv._root) if _srv._root else []

        def grep(pat: str, rx: bool) -> tuple[list[dict], str]:
            """Hits of ``pat`` over the parent, then each federated child."""
            found: list[dict] = []
            hits, used = _search(
                _srv._root,
                pattern=pat,
                glob=glob,
                max_results=max_results,
                regex=rx,
                case_sensitive=case_sensitive,
            )
            found += [
                {"scope": "parent", "file": h.file, "line": h.line, "text": h.text}
                for h in hits
            ]
            for child in children:
                try:
                    child_hits, _ = _search(
                        child,
                        pattern=pat,
                        glob=glob,
                        max_results=max_results,
                        regex=rx,
                        case_sensitive=case_sensitive,
                    )
                except Exception:
                    continue
                found += [
                    {
                        "scope": child.name,
                        "file": h.file,
                        "line": h.line,
                        "text": h.text,
                    }
                    for h in child_hits
                ]
            return found, used

        all_hits, backend = grep(pattern, regex)

        # A sentence is understood, not only grepped. A plain-word query
        # that matched nothing literally gets the same second chance.
        intent = interpret(pattern)
        if not intent.sentence and not all_hits and plain_words(pattern) >= 2:
            intent = interpret(pattern, force=True)
        extra: dict = {}
        if intent.sentence and not count_only:
            if intent.identifiers:
                alternation = "|".join(re.escape(i) for i in intent.identifiers[:6])
                seen = {(h["scope"], h["file"], h["line"]) for h in all_hits}
                more_hits, _ = grep(rf"\b(?:{alternation})\b", True)
                all_hits += [
                    h
                    for h in more_hits
                    if (h["scope"], h["file"], h["line"]) not in seen
                ]
            exact, scored = _understand(intent)
            extra.update(intent.describe())
            symbols: list[dict] = []
            docs: list[dict] = []
            keys: set[tuple] = set()
            for row in exact:
                key = (row.get("scope", "parent"), row["file"], row["lines"])
                if key not in keys:
                    keys.add(key)
                    symbols.append(
                        {
                            "scope": row.get("scope", "parent"),
                            "kind": row["kind"],
                            "name": row["name"],
                            "file": row["file"],
                            "lines": row["lines"],
                        }
                    )
            for _cover, scope, r in scored:
                lines = (
                    f"{r.start_line}-{r.end_line}" if r.end_line else str(r.start_line)
                )
                key = (scope, r.file_path, lines)
                if key in keys:
                    continue
                keys.add(key)
                row = {
                    "scope": scope,
                    "kind": r.kind,
                    "name": r.name,
                    "file": r.file_path,
                    "lines": lines,
                }
                if r.kind == "md_section":
                    if len(docs) < 3:
                        docs.append(row)
                elif len(symbols) < 8:
                    if r.docstring:
                        row["doc"] = r.docstring[:100]
                    symbols.append(row)
            from codegraph.server._budget import drop_parent_scope

            drop_parent_scope(symbols)
            drop_parent_scope(docs)
            extra["symbols"] = symbols
            extra["docs"] = docs
            extra.update(_sentence_verdict(intent, len(exact), scored))
            # With no grep line to show, the sentence's fields get the room.
            _fit_extra(
                extra,
                SENTENCE_EXTRA_BYTES if all_hits else PATTERN_ANSWER_BYTES - 500,
            )

        # Most useful first, so the cut keeps definitions and central files.
        all_hits = rank_hits(all_hits, _central())
        # Apply max_results across the whole federation as a soft cap
        all_hits = all_hits[: max_results * (1 + len(children))]

        roots = [p for p in [_srv._root, *children] if p is not None]
        with_scope = any(h["scope"] != "parent" for h in all_hits)
        extra_bytes = answer_bytes(extra) if extra else 0
        # In a federated project the answer names the commit of each scope
        # it shows. Added here rather than by the tool wrapper, so the
        # budget below counts it.
        scopes: dict | None = None
        if children:
            try:
                scopes = _srv._scope_commits(_srv._root)
            except Exception:  # best-effort note: never fail the answer on it
                scopes = None

        def build(slack: int) -> dict:
            payload: dict = {
                "pattern": pattern,
                "glob": glob or None,
                "backend": backend,
                **_hits_payload(
                    all_hits,
                    pattern,
                    regex,
                    case_sensitive,
                    with_scope=with_scope,
                    roots=roots,
                    count_only=count_only,
                    reserve=extra_bytes + slack,
                    limit=max_results,
                ),
            }
            if intent.sentence and not count_only:
                own = dict(extra)
                # A sentence's own verdict stands. Lines matching it literally
                # (often a comment quoting it) lift a low verdict to medium.
                if payload.get("total") and own.get("confidence") == "low":
                    for k in ("why", "next"):
                        own.pop(k, None)
                    own["confidence"] = "medium"
                payload.update(own)
            else:
                payload.update(_grep_verdict(payload, pattern, glob, regex))
            if scopes:
                payload["scopes"] = _shown_scopes(payload, scopes)
            return payload

        return _fit_pattern_answer(build)

    def _attach_rationale(conn, hits: list[dict]) -> None:
        """Give each function / class hit the rationale comments attached to
        it (at most RATIONALE_PER_SYMBOL, text cut short), as `rationale`;
        drops the internal `_id`. A graph without the rationale table (a
        child indexed by an older cgh) answers without them."""
        from codegraph.analysis.rationale import (
            RATIONALE_PER_SYMBOL,
            RATIONALE_TEXT_CAP,
            for_symbol_ids,
            item,
        )

        ids = [h["_id"] for h in hits if h.get("_id")]
        try:
            by_id = for_symbol_ids(conn, ids)
        except Exception:  # older graph without the table, see the docstring
            by_id = {}
        for hit in hits:
            rows = by_id.get(hit.pop("_id", None) or "", [])
            if rows:
                hit["rationale"] = [
                    item(r, RATIONALE_TEXT_CAP, with_symbol=False)
                    for r in rows[:RATIONALE_PER_SYMBOL]
                ]

    @mcp.tool()
    @_logged_tool
    def rationale(target: str = "", tag: str = "", limit: int = 50) -> str:
        """
        Rationale comments: the comments that say WHY code is the way it
        is, read from the source at index time. A comment counts when it
        opens with WHY:, NOTE:, HACK:, SAFETY:, INVARIANT:, IMPORTANT:,
        WARNING: or DECISION: (an owner in parentheses is fine, as in
        HACK(joy):), or when it cites an ADR (ADR-0007, docs/adr/0007-x.md).
        Each is attached to the function or class right below it, else the
        innermost one around it, else the module. Read these before
        changing code that looks odd: they record the constraint behind it.

        Args:
          target: a symbol name (bare, or Class.method; a class also lists
                  its members'), a file path (absolute or a path suffix like
                  "app/store.py"), or "" for every one in the index
          tag:    keep one tag only (WHY, NOTE, ..., ADR for a comment that
                  only cites an ADR); case-insensitive
          limit:  max rows per scope (default 50, at most 200)

        Returns {target, tag, total, truncated, rationale: [{scope, file,
        line, tag, text, symbol, symbol_kind, adr?, adr_file?}]}; adr_file
        is the indexed Markdown file the ADR reference resolves to.
        Supported: Python, JavaScript / TypeScript, Vue, Go, Rust, Java,
        C#, Ruby, Terraform, SQL. Federated, each row tagged with `scope`.
        """
        from codegraph.analysis.federation import federate_scoped
        from codegraph.analysis.rationale import clamp_limit, payload, query

        cap = clamp_limit(limit)
        per_scope, warnings = federate_scoped(
            _get_conn, _srv._root, lambda conn: query(conn, target, tag, cap + 1)
        )
        out = payload(
            per_scope,
            target,
            tag,
            cap,
            short_path=lambda p: _short_path(p, _srv._root),
        )
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

    def _definitions_query(name: str, role: str = "", layer: str = ""):
        """The graph query symbol_lookup runs for one name: functions,
        classes, Terraform blocks and Markdown sections, with the
        rationale attached and the role / layer filters applied."""

        def query(conn):
            out = []
            for row in conn.find_nodes(
                "Function",
                where={"name": name},
                return_fields=[
                    "name",
                    "file_path",
                    "start_line",
                    "end_line",
                    "docstring",
                    "id",
                ],
            ):
                out.append(
                    {
                        "kind": "function",
                        "name": row["name"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["docstring"] or "")[:120],
                        "_id": row["id"],
                    }
                )
            for row in conn.find_nodes(
                "Class",
                where={"name": name},
                return_fields=[
                    "name",
                    "file_path",
                    "start_line",
                    "end_line",
                    "docstring",
                    "id",
                ],
            ):
                out.append(
                    {
                        "kind": "class",
                        "name": row["name"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["docstring"] or "")[:120],
                        "_id": row["id"],
                    }
                )
            # Terraform blocks, by address (var.region, google_x.y) or by
            # bare block name.
            for hit in tf_lookup(conn, name):
                out.append(
                    {
                        "kind": hit["kind"],
                        "name": hit["name"],
                        "type": hit["type"],
                        "file": hit["file"],
                        "lines": f"{hit['start_line']}-{hit['end_line']}",
                    }
                )
            for row in conn.find_nodes(
                "MdSection",
                contains={"title": name},
                return_fields=[
                    "title",
                    "file_path",
                    "start_line",
                    "end_line",
                    "body_preview",
                    "anchor",
                ],
            ):
                out.append(
                    {
                        "kind": "md_section",
                        "name": row["title"],
                        "file": row["file_path"],
                        "lines": f"{row['start_line']}-{row['end_line']}",
                        "doc": (row["body_preview"] or "")[:120],
                        "anchor": row["anchor"],
                    }
                )
            _attach_rationale(conn, out)
            if role or layer:
                cache: dict[str, tuple[str, str]] = {}
                kept = []
                for hit in out:
                    fp = hit.get("file")
                    if not fp:
                        continue
                    if fp not in cache:
                        cache[fp] = _file_role_layer(conn, fp)
                    r, lyr = cache[fp]
                    if role and r != role:
                        continue
                    if layer and lyr != layer:
                        continue
                    kept.append(hit)
                return kept
            return out

        return query

    @mcp.tool()
    @_logged_tool
    def symbol_lookup(
        name: str, role: str = "", layer: str = "", format: str = ""
    ) -> str:
        """
        Find where a symbol (function, class, TF resource) is defined.
        Returns file path, line range, type, and docstring snippet, plus
        a `scope` tag (parent / <subrepo-name>) when federation is on.
        Use this instead of grepping files.

        Optional `role` / `layer` filters keep only definitions whose File
        node carries that exact role / layer (empty = no filter).

        Each definition carries its `name` (the section title for a
        markdown section, which matches on a title substring).

        Terraform blocks are found by address too (var.region,
        google_x.y, module.m, local.l, data.t.n, output.o).

        A function or class whose code carries rationale comments (WHY:,
        NOTE:, HACK:, SAFETY:, INVARIANT:, ... or an ADR reference) lists
        the first few in `rationale` ({tag, line, text, adr?, adr_file?});
        the `rationale` tool returns them all.

        Code definitions come before Markdown sections and tests, then
        the most imported and most recently changed files. A sentence
        ("where are webhooks retried") is understood: the identifiers it
        names are looked up, and its words and their synonyms go through
        the FTS; `interpreted_as` and `expanded` say what was searched.
        Every answer has `confidence` (high, medium, low); a low or empty
        one says `why` and gives `next` (a call to try) and, for a near
        miss, `candidates` (close names).

        `format="text"` answers a grep-like layout instead of JSON (default:
        the [mcp] answer_format setting).
        """
        from codegraph.analysis import on_demand

        results, warnings = _federate(_definitions_query(name, role, layer))
        # Nothing by that name: a file written since its last index may
        # define it. Index those now and look again, once.
        fresh = [] if results else on_demand.catch_up(_srv._root)
        if fresh:
            results, warnings = _federate(_definitions_query(name, role, layer))
        if warnings and not (role or layer):
            # Same fallback as search_symbols: a child whose owner holds the
            # graph write lock still resolves the name through its FTS index.
            results, warnings = _retry_children_via_fts(
                results,
                warnings,
                lambda scopes: child_fts_symbol_lookup(_srv._root, name, scopes),
                lambda hit, scope: {
                    "kind": hit.kind,
                    "name": hit.name,
                    "file": hit.file_path,
                    "lines": f"{hit.start_line}-{hit.end_line}",
                    "doc": hit.docstring[:120],
                    "scope": scope,
                },
            )
        from codegraph.analysis.query_intent import (
            call,
            interpret,
            plain_words,
            rank_definitions,
        )

        intent = interpret(name)
        if not intent.sentence and not results and plain_words(name) >= 2:
            intent = interpret(name, force=True)
        verdict: dict
        if intent.sentence and not (role or layer):
            exact, scored = _understand(intent)
            seen = {(r.get("scope", "parent"), r["file"], r["lines"]) for r in results}
            added: list[dict] = []
            for row in exact:
                key = (row.get("scope", "parent"), row["file"], row["lines"])
                if key not in seen:
                    seen.add(key)
                    added.append(row)
            for _cover, scope, r in scored:
                if len(added) >= 8 + len(exact):
                    break
                lines = (
                    f"{r.start_line}-{r.end_line}" if r.end_line else str(r.start_line)
                )
                key = (scope, r.file_path, lines)
                if key in seen:
                    continue
                seen.add(key)
                added.append(
                    {
                        "kind": r.kind,
                        "name": r.name,
                        "file": r.file_path,
                        "lines": lines,
                        "doc": (r.docstring or "")[:120],
                        "scope": scope,
                    }
                )
            results = [*results, *added]
            verdict = {
                **intent.describe(),
                **_sentence_verdict(intent, len(exact), scored),
            }
        else:
            results = rank_definitions(results, _central())
            if any(r.get("kind") != "md_section" for r in results):
                verdict = {"confidence": "high"}
            elif results:
                # Only section titles holding the name: no code defines it.
                verdict = {"confidence": "medium"}
            else:
                verdict = {
                    "confidence": "low",
                    "why": (
                        f"No function, class, Terraform block or Markdown section "
                        f"is named {name!r}"
                        + (" with that role / layer." if role or layer else ".")
                    ),
                }
            if verdict["confidence"] != "high":
                near = [n for n in _near_names(name) if n != name]
                if near:
                    verdict["candidates"] = near
                    verdict["next"] = call("symbol_lookup", near[0])
                elif not results:
                    verdict["next"] = (
                        call("search_symbols", name)
                        if re.fullmatch(r"[\w.]+", name or "")
                        else call("fts_search", name)
                    )
        if not results:
            payload = {"found": False, "name": name, **verdict}
            if warnings:
                payload["partial"] = True
                payload["warnings"] = warnings
            return dumps_answer(payload)
        out = {"found": True, "name": name, "definitions": results, **verdict}
        if fresh:
            out["indexed_now"] = fresh
            out["note"] = on_demand.note(fresh)
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

    @mcp.tool()
    @_logged_tool
    def find_callers(fn_name: str, format: str = "") -> str:
        """
        Find all functions that call `fn_name`. Federated across subrepos,
        each result tagged with `scope`. Note: cross-repo CALLS edges are
        not inferred (each subrepo's graph is canonical for its own code).
        Calls are matched by name: when several functions share `fn_name`
        (a method and its test double), each caller lists the matched
        definitions' files in `targets`.

        A `Class.method` name also lists the callers of the interface
        methods it implements (a base class, an ABC, a Protocol the class
        conforms to), since a call through the interface links to the
        interface's method; those rows carry `via` ("KmsClient.encrypt").

        Each row carries `resolution`: "resolved" (the call's shape fixes
        the target: same file, an import naming it, a known receiver
        class), "name" (matched by name, one definition), "ambiguous"
        (matched by name, several definitions) or "interface" (reached
        only through an interface method). Treat name and ambiguous as
        guesses.

        For a Terraform address (var.region, google_x.y, module.m) it
        returns each block that uses it.

        `format="text"` answers a grep-like layout instead of JSON (default:
        the [mcp] answer_format setting).
        """

        from codegraph.analysis import on_demand
        from codegraph.analysis.callers import callers_of

        callers, warnings = _federate(lambda conn: callers_of(conn, fn_name))
        # No caller: one may sit in a file written since its last index.
        fresh = [] if callers else on_demand.catch_up(_srv._root)
        if fresh:
            callers, warnings = _federate(lambda conn: callers_of(conn, fn_name))
        out = {"fn": fn_name, "callers": callers}
        if fresh:
            out["indexed_now"] = fresh
            out["note"] = on_demand.note(fresh)
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

    @mcp.tool()
    @_logged_tool
    def find_callees(fn_name: str, max_depth: int = 1) -> str:
        """
        Find the functions `fn_name` calls. With `max_depth=1` (default)
        that is the direct callees. With `max_depth>1` it walks the CALLS
        edges forward transitively, so one call returns the ordered call
        chain (each callee carries its `depth`, 1 = direct) instead of
        forcing a separate lookup per hop. Use it to trace a flow in a
        single call.

        Federated across subrepos; cross-repo CALLS edges are not inferred
        (each subrepo's graph is canonical for its own code). Like every
        name-matched CALLS reach it can over-count: a listed callee may
        belong to a same-named function elsewhere. Each callee carries
        `resolution` ("resolved", "name", "ambiguous", see find_callers),
        so the guessed rows are told apart. `truncated` is set when the
        fan-out or total cap was hit.

        A call to an interface method (a Protocol or ABC method) that has
        exactly one implementation in the scope is followed to it: the
        implementation is listed right after, at the same depth, with
        `resolution` "interface" and `via` naming the interface method.
        With several implementations the walk stops there and the row
        carries `implementations` (their count). `Class.method` or
        `module.function` starts from that definition only.

        For a Terraform address it returns the blocks that block references.
        """
        depth_cap = max(1, min(int(max_depth), _CALLEE_DEPTH_CAP))
        state = {"truncated": False}

        def query(conn):
            from codegraph.analysis.interfaces import (
                InterfaceResolver,
                label,
                qualified_methods,
            )
            from codegraph.analysis.provenance import (
                INTERFACE,
                RESOLVED,
                EdgeProvenance,
            )

            prov = EdgeProvenance(conn)
            resolver = InterfaceResolver(conn)
            # `Class.method` / `module.function` starts from those functions.
            start_ids = qualified_methods(conn, fn_name) if "." in fn_name else []
            seen: set[str] = {fn_name}  # names already expanded (cycle guard)
            frontier = [fn_name]
            out: list[dict] = []
            depth = 0

            def dispatched(row: dict) -> dict | None:
                """The implementation a call to an interface method lands
                on, as a callee row, when the interface has exactly one in
                this scope; the interface row gets `implementations` when
                it has several."""
                dst = str(row.get("dst_id") or "")
                if not dst or not resolver.is_interface(dst):
                    return None
                impls = resolver.implementations(dst)
                if len(impls) != 1:
                    if impls:
                        out[-1]["implementations"] = len(impls)
                    return None
                found = conn.find_nodes(
                    "Function",
                    where={"id": impls[0]},
                    return_fields=["name", "file_path", "start_line"],
                    limit=1,
                )
                if not found:
                    return None
                return {
                    "callee": found[0]["name"],
                    "file": found[0]["file_path"],
                    "line": found[0]["start_line"],
                    "depth": depth,
                    "resolution": INTERFACE,
                    "via": label(dst),
                }

            while frontier and depth < depth_cap:
                depth += 1
                nxt: list[str] = []
                for name in frontier:
                    if depth == 1 and start_ids:
                        rows = [
                            r
                            for sid in start_ids
                            for r in conn.find_neighbors(
                                "CALLS",
                                src_key=sid,
                                return_src=["id"],
                                return_dst=["id", "name", "file_path", "start_line"],
                                limit=_CALLEE_FANOUT_CAP,
                            )
                        ]
                    else:
                        rows = conn.find_neighbors(
                            "CALLS",
                            src_where={"name": name},
                            return_src=["id"],
                            return_dst=["id", "name", "file_path", "start_line"],
                            limit=_CALLEE_FANOUT_CAP,
                        )
                    resolutions = prov.of_rows(rows)
                    if "." in name:
                        # A Terraform address: the blocks it references.
                        rows = rows + [
                            {**r, "dst_name": r["dst_address"]}
                            for r in tf_used_by(conn, name, limit=_CALLEE_FANOUT_CAP)
                        ]
                    if len(rows) >= _CALLEE_FANOUT_CAP:
                        state["truncated"] = True
                    for i, row in enumerate(rows):
                        callee = row["dst_name"]
                        out.append(
                            {
                                "callee": callee,
                                "file": row["dst_file_path"],
                                "line": row["dst_start_line"],
                                "depth": depth,
                                # Terraform rows name their address exactly.
                                "resolution": resolutions[i]
                                if i < len(resolutions)
                                else RESOLVED,
                            }
                        )
                        if callee not in seen:
                            seen.add(callee)
                            nxt.append(callee)
                        impl = dispatched(row) if i < len(resolutions) else None
                        if impl is not None:
                            # Not a call of its own: the same hop, landing
                            # on the interface's only implementation.
                            out.append(impl)
                            if impl["callee"] not in seen:
                                seen.add(impl["callee"])
                                nxt.append(impl["callee"])
                    if len(out) >= _CALLEE_TOTAL_CAP:
                        state["truncated"] = True
                        return out
                frontier = nxt
            return out

        from codegraph.analysis import on_demand

        callees, warnings = _federate(query)
        # Nothing found: the function may sit in a file written since its
        # last index.
        fresh = [] if callees else on_demand.catch_up(_srv._root)
        if fresh:
            callees, warnings = _federate(query)
        out = {"fn": fn_name, "max_depth": depth_cap, "callees": callees}
        if fresh:
            out["indexed_now"] = fresh
            out["note"] = on_demand.note(fresh)
        if state["truncated"]:
            out["truncated"] = True
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

    @mcp.tool()
    @_logged_tool
    def imports_of(file_path: str) -> str:
        """
        Return all modules imported by `file_path`. Federated: the file may
        live in the parent or in any subrepo, we query all and aggregate.
        Pass a path relative to the parent's repo root or absolute. Each
        row's `kind` is "" for a plain import, "reexport" (JS / TS
        `export ... from`) or "dynamic" (`import('./x')`).
        """
        from codegraph.analysis import on_demand

        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)
        status = (
            on_demand.ensure_indexed(_srv._root, Path(file_path))
            if _srv._root
            else "fresh"
        )

        def query(conn):
            return [
                {
                    "module": row["dst_path"],
                    "symbol": row.get("edge_symbol", ""),
                    "kind": row.get("edge_kind") or "",
                }
                for row in conn.find_neighbors(
                    "IMPORTS",
                    src_key=file_path,
                    return_dst=["path"],
                    return_edge=["symbol", "kind"],
                )
            ]

        imports, warnings = _federate(query)
        out = {"file": file_path, "imports": imports, **on_demand.answer_fields(status)}
        if warnings:
            out["partial"] = True
            out["warnings"] = warnings
        return dumps_answer(out)

    @mcp.tool()
    @_logged_tool
    def indexed_files(pattern: str = "", limit: int = 200, path: str = "") -> str:
        """
        List the files in this repo's graph index, or check one file.

        Without `path`: every indexed File path containing `pattern` (all
        when empty), sorted, as `files` (the first `limit`) plus `total`.
        With `path` (relative to the repo root, or absolute): `indexed` is
        true when that exact file is in the index. Parent scope only, like
        `cgh files`; a subrepo answers from its own index. Unlike the FTS
        index, this also sees files that define no symbol.
        """
        conn = _get_conn()
        if path:
            if not os.path.isabs(path) and _srv._root:
                path = str(_srv._root / path)
            hit = conn.query_node_field("File", "path", path, "path")
            return dumps_answer({"path": path, "indexed": hit is not None})
        found = conn.find_nodes(
            "File",
            contains={"path": pattern} if pattern else None,
            return_fields=["path"],
            order_by=["path"],
        )
        paths = [r["path"] for r in found]
        return dumps_answer(
            {"pattern": pattern, "total": len(paths), "files": paths[: max(limit, 0)]}
        )

    def _file_role_layer(conn, file_path: str) -> tuple[str, str]:
        """Return (role, layer) for a File node, ('', '') when unknown."""
        nodes = conn.find_nodes(
            "File",
            where={"path": file_path},
            return_fields=["role", "layer"],
            limit=1,
        )
        if not nodes:
            return "", ""
        return (nodes[0].get("role") or "", nodes[0].get("layer") or "")

    @mcp.tool()
    @_logged_tool
    def search_symbols(
        query: str,
        limit: int = 20,
        role: str = "",
        layer: str = "",
        kinds: str = "",
        name_only: bool = False,
        format: str = "",
    ) -> str:
        """
        Find symbols (functions, classes, TF resources) whose NAME contains
        the query (substring match). Use it for a half-known name; for code
        described in words use fts_search. Federated, `limit` is per scope
        (all kinds together, an exact name first, then a prefix match),
        results are concatenated. Returns {query, total, results: [{kind,
        name, file, line, ...}]}; `truncated` and a `hint` say when matches
        were left out. Results carry `scope` only when some come from a
        federated child. The answer stays under about 10 KB.

        Optional `role` / `layer` filters keep only symbols whose File node
        carries that exact role / layer (empty = no filter). Useful to scope
        a search to e.g. role="router" or layer="domain".

        Optional `kinds` (comma list of function, class, tf_resource,
        tf_var, md_section; empty = all) limits the graph match to those
        kinds. `name_only=True` matches the name or section title only,
        not a TF type / kind or a section's body preview. Both are echoed
        back when set.

        Terraform kinds for `kinds`: tf_resource, tf_data, tf_module,
        tf_module_arg (a module call's input, module.m.<arg>), tf_local,
        tf_provider, tf_moved, tf_import, tf_removed, tf_var.

        A sentence instead of a name is understood like in symbol_lookup
        (`interpreted_as`, `expanded`). Every answer has `confidence`; a
        low one says `why` and gives `next`.

        `format="text"` answers a grep-like layout instead of JSON (default:
        the [mcp] answer_format setting).
        """
        wanted = {k.strip() for k in kinds.split(",") if k.strip()}

        def _want(kind: str) -> bool:
            return not wanted or kind in wanted

        def run(conn):
            out = []
            for label, kind in [("Function", "function"), ("Class", "class")]:
                if not _want(kind):
                    continue
                # The exact name first: the substring match below stops at
                # `limit` rows and could leave it out.
                seen: set[tuple] = set()
                rows = [
                    *conn.find_nodes(
                        label,
                        where={"name": query},
                        return_fields=["name", "file_path", "start_line"],
                        limit=limit,
                    ),
                    *conn.find_nodes(
                        label,
                        contains={"name": query},
                        return_fields=["name", "file_path", "start_line"],
                        limit=limit,
                    ),
                ]
                for row in rows:
                    key = (row["name"], row["file_path"], row["start_line"])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        {
                            "kind": kind,
                            "name": row["name"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                        }
                    )
            for hit in tf_search(conn, query, limit, name_only, wanted):
                out.append(
                    {
                        "kind": hit["kind"],
                        "name": hit["name"],
                        "type": hit["type"],
                        "file": hit["file"],
                        "line": hit["start_line"],
                    }
                )
            if _want("md_section"):
                match = (
                    {"title": query}
                    if name_only
                    else {"title": query, "body_preview": query}
                )
                for row in conn.find_nodes(
                    "MdSection",
                    contains=match,
                    return_fields=[
                        "title",
                        "file_path",
                        "start_line",
                        "level",
                        "anchor",
                    ],
                    limit=limit,
                ):
                    out.append(
                        {
                            "kind": "md_section",
                            "name": row["title"],
                            "file": row["file_path"],
                            "line": row["start_line"],
                            "level": row["level"],
                            "anchor": row["anchor"],
                        }
                    )
            if role or layer:
                # Filter each hit by its File node's role / layer. Cache
                # per-file lookups so repeated hits in the same file cost one
                # query. Markdown / TF hits without a File node drop out.
                cache: dict[str, tuple[str, str]] = {}
                kept = []
                for hit in out:
                    fp = hit.get("file")
                    if not fp:
                        continue
                    if fp not in cache:
                        cache[fp] = _file_role_layer(conn, fp)
                    r, lyr = cache[fp]
                    if role and r != role:
                        continue
                    if layer and lyr != layer:
                        continue
                    kept.append(hit)
                return kept
            return out

        results, warnings = _federate(run)
        if warnings and not (role or layer):
            # A child whose own owner holds the graph write lock cannot be
            # opened read-only from here. Retry it over its FTS index, which
            # takes concurrent readers, so it stays in the results instead of
            # coming back as a partial scope. The role / layer filters need
            # the child's File nodes, so a filtered search keeps the warning.
            results, warnings = _retry_children_via_fts(
                results,
                warnings,
                lambda scopes: child_fts_symbol_search(
                    _srv._root, query, limit, scopes
                ),
                lambda hit, scope: {
                    "kind": hit.kind,
                    "name": hit.name,
                    "file": hit.file_path,
                    "line": hit.start_line,
                    "scope": scope,
                },
            )
        from codegraph.analysis.query_intent import (
            call,
            interpret,
            plain_words,
            rank_definitions,
        )
        from codegraph.server._budget import cap_answer, drop_parent_scope, dumps_answer

        # Each kind used to bring its own `limit` rows, so one query could
        # answer four times `limit` rows per scope: keep the best `limit` of
        # them, an exact name first, then a name starting with the query.
        # Then code before sections and tests, the most imported files and
        # the most recently changed ones (rank_definitions, a stable sort).

        q = query.lower()

        def _rank(hit: dict) -> int:
            name = str(hit.get("name") or "").lower()
            return 0 if name == q else 1 if name.startswith(q) else 2

        # A sentence finds nothing by substring: understand it instead.
        intent = interpret(query)
        if not intent.sentence and not results and plain_words(query) >= 2:
            intent = interpret(query, force=True)
        understood = intent.sentence and not (role or layer)
        verdict: dict = {}
        if understood:
            exact, scored = _understand(intent, limit=max(limit, 8))
            seen = {
                (h.get("scope", "parent"), h["file"], h.get("line")) for h in results
            }
            added: list[dict] = []
            for row in exact:
                line = int(str(row["lines"]).split("-")[0] or 0)
                key = (row.get("scope", "parent"), row["file"], line)
                if key not in seen:
                    seen.add(key)
                    added.append(
                        {
                            "kind": row["kind"],
                            "name": row["name"],
                            "file": row["file"],
                            "line": line,
                            "scope": row.get("scope", "parent"),
                        }
                    )
            for _cover, scope, r in scored:
                if wanted and r.kind not in wanted:
                    continue
                key = (scope, r.file_path, r.start_line)
                if key in seen:
                    continue
                seen.add(key)
                added.append(
                    {
                        "kind": r.kind,
                        "name": r.name,
                        "file": r.file_path,
                        "line": r.start_line,
                        "scope": scope,
                    }
                )
            results = [*results, *added]
            verdict = {
                **intent.describe(),
                **_sentence_verdict(intent, len(exact), scored),
            }
            order = None  # keep the understanding's own order
        else:
            results = rank_definitions(results, _central())
            order = _rank

        by_scope: dict[str, list[dict]] = {}
        for hit in results:
            by_scope.setdefault(hit.get("scope", "parent"), []).append(hit)
        total = len(results)
        results = [
            hit
            for hits in by_scope.values()
            for hit in (sorted(hits, key=order) if order else hits)[: max(0, limit)]
        ]
        if not understood:
            best = min((_rank(h) for h in results), default=None)
            if best is not None and best <= 1:
                verdict = {"confidence": "high"}
            elif best is not None:
                verdict = {"confidence": "medium"}
            else:
                verdict = {
                    "confidence": "low",
                    "why": f"No symbol name contains {query!r}"
                    + (" with these filters." if role or layer or kinds else "."),
                }
                near = _near_names(query)
                if near:
                    verdict["candidates"] = near
                    verdict["next"] = call("symbol_lookup", near[0])
                else:
                    verdict["next"] = call("fts_search", query)
        drop_parent_scope(results)
        payload: dict = {"query": query, "total": total, "results": results}
        if len(results) < total:
            payload["truncated"] = True
            payload["hint"] = (
                f"{total - len(results)} more match(es) past `limit`: use a "
                "longer query, `kinds` or `name_only` to narrow it."
            )
        if kinds:
            payload["kinds"] = kinds
        if name_only:
            payload["name_only"] = True
        if role:
            payload["role"] = role
        if layer:
            payload["layer"] = layer
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        payload.update(verdict)
        cap_answer(
            payload,
            ["results"],
            "Answer cut to fit: use a longer query, `kinds` or `name_only` "
            "to narrow it.",
        )
        return dumps_answer(payload)

    @mcp.tool()
    @_logged_tool
    def subgraph(file_path: str, depth: int = 1, max_tokens: int = 0) -> str:
        """
        Return files related to `file_path` within `depth` import hops.
        Federated. Inter-repo edges are not modeled, each subrepo's IMPORTS
        graph is canonical for its own files. Useful for blast radius
        within a single scope; results are concatenated across scopes.

        `max_tokens` (0 = no budget) caps the answer's approximate size
        (about 4 characters per token of JSON): the farthest files go
        first (with depth 1, from the longer of the two lists), and the
        answer gets `truncated: true` and `dropped` (how many).

        A repo file the graph does not hold yet is indexed first
        (`indexed_now: true`).
        """
        from codegraph.analysis import on_demand

        if not os.path.isabs(file_path) and _srv._root:
            file_path = str(_srv._root / file_path)
        fresh = on_demand.answer_fields(
            on_demand.ensure_indexed(_srv._root, Path(file_path))
            if _srv._root
            else "fresh"
        )

        if depth == 1:

            def run_deps(conn):
                return [
                    {
                        "kind": "depends_on",
                        "file": row["dst_path"],
                        "lang": row["dst_lang"],
                    }
                    for row in conn.find_neighbors(
                        "IMPORTS", src_key=file_path, return_dst=["path", "lang"]
                    )
                ]

            def run_rdeps(conn):
                return [
                    {
                        "kind": "depended_by",
                        "file": row["src_path"],
                        "lang": row["src_lang"],
                    }
                    for row in conn.find_neighbors(
                        "IMPORTS", dst_key=file_path, return_src=["path", "lang"]
                    )
                ]

            deps_all, w1 = _federate(run_deps)
            rdeps_all, w2 = _federate(run_rdeps)
            depends_on = [{k: v for k, v in d.items() if k != "kind"} for d in deps_all]
            depended_by = [
                {k: v for k, v in d.items() if k != "kind"} for d in rdeps_all
            ]
            payload = {
                "file": file_path,
                "depth": depth,
                "depends_on": depends_on,
                "depended_by": depended_by,
                **fresh,
            }
            warnings = w1 + w2
            if warnings:
                payload["partial"] = True
                payload["warnings"] = warnings
            return _within_budget(payload, ["depends_on", "depended_by"], max_tokens)

        # depth >= 2, recursive traversal via the GraphDB helper
        def run_reach(conn):
            rows = [
                {"file": row["path"], "lang": row["lang"]}
                for row in conn.reach_via_edge(
                    "IMPORTS",
                    file_path,
                    max_depth=int(depth),
                    return_fields=["path", "lang"],
                )
            ]
            if max_tokens > 0:
                # Nearest first, so a budget trims the farthest files.
                hops = _import_hops(conn, file_path, int(depth))
                rows.sort(key=lambda r: (hops.get(r["file"], depth + 1), r["file"]))
            return rows

        deps, warnings = _federate(run_reach)
        payload = {"file": file_path, "depth": depth, "reachable": deps, **fresh}
        if warnings:
            payload["partial"] = True
            payload["warnings"] = warnings
        return _within_budget(payload, ["reachable"], max_tokens)

    def _import_hops(conn, start: str, depth: int) -> dict[str, int]:
        """{file: import hops from ``start``} within ``depth`` hops."""
        hops: dict[str, int] = {}
        frontier = [start]
        for level in range(1, depth + 1):
            nxt: list[str] = []
            for key in frontier:
                for row in conn.find_neighbors(
                    "IMPORTS", src_key=key, return_dst=["path"]
                ):
                    path = row["dst_path"]
                    if path not in hops and path != start:
                        hops[path] = level
                        nxt.append(path)
            frontier = nxt
        return hops

    def _within_budget(payload: dict, keys: list[str], max_tokens: int) -> str:
        """The payload as JSON, trimmed to ``max_tokens`` when one is set."""
        if max_tokens > 0:
            from codegraph.server._budget import fit_tokens

            fit_tokens(payload, keys, max_tokens)
        return dumps_answer(payload)
