# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-12
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: How a CALLS edge was obtained, derived at query time from the
#              call sites the caller recorded (analysis/call_rules.py). A
#              CALLS edge carries no attribute, so EdgeProvenance reads the
#              caller's sites and tells whether one of them pins the target
#              down (the call's shape names it) or the edge is a match on
#              the callee's name. The vocabulary is closed and small:
#                resolved   the call's shape fixes the target: a precise
#                           resolver hit, a bare call to a function of the
#                           same file, an import naming the target's file,
#                           self.f() on the caller's class or a base, or a
#                           receiver whose class is known
#                name       matched by the callee's name only (receiver of
#                           unknown type, no import binding it, a language
#                           without call shapes), one definition of it
#                ambiguous  matched by name, and the caller links to several
#                           definitions of that name
#                interface  reached through an interface method the target
#                           implements (set by the traversals, not here)

from __future__ import annotations

from typing import Any

from codegraph.analysis.call_rules import BARE, CLS, EXTERNAL, MOD, SELF, real

RESOLVED = "resolved"
NAME = "name"
AMBIGUOUS = "ambiguous"
INTERFACE = "interface"
RESOLUTIONS = (RESOLVED, NAME, AMBIGUOUS, INTERFACE)
# The values that mean "a guess": the CLI marks these rows.
GUESSED = frozenset({NAME, AMBIGUOUS})

# Most certain first, so several edges of one caller row keep the best.
_RANK = {RESOLVED: 0, INTERFACE: 1, NAME: 2, AMBIGUOUS: 3}


def best(values: list[str]) -> str:
    """The most certain of ``values`` (several edges behind one row)."""
    return min(values, key=lambda v: _RANK.get(v, len(_RANK))) if values else NAME


def _file_of(fn_id: str) -> str:
    return fn_id.rsplit("::", 1)[0] if "::" in fn_id else ""


def _class_of(fn_id: str) -> str:
    qual = fn_id.rsplit("::", 1)[-1]
    return qual.rsplit(".", 1)[0] if "." in qual else ""


def _name_of(fn_id: str) -> str:
    return fn_id.rsplit("::", 1)[-1].rsplit(".", 1)[-1]


class EdgeProvenance:
    """Per-query cache of call sites, out-edges and class lineages.

    ``of(from_id, to_id)`` answers for one CALLS edge. ``same`` lists the
    functions of the target's name the caller links to, when the query
    already holds them (a caller's whole out-edge set for that name);
    otherwise they are read from the graph."""

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self._sites: dict[str, list[tuple[str, ...]]] = {}
        self._out: dict[str, dict[str, str]] = {}
        self._lineage: dict[str, frozenset[str]] = {}

    def prefetch(self, from_ids: list[str] | set[str]) -> None:
        """Load the call sites of ``from_ids`` in one batch."""
        todo = sorted({i for i in from_ids if i and i not in self._sites})
        if not todo:
            return
        for i in todo:
            self._sites[i] = []
        reader = getattr(self.conn, "call_sites_from", None)
        if reader is None:
            return  # a graph without recorded sites: everything is by name
        try:
            rows = reader(todo)
        except Exception:
            # Best-effort read-only enrichment: without sites every edge
            # reads as a name match, which is the conservative answer.
            return
        for row in rows:
            padded = (*row, *("",) * 7)[:7]
            from_id, file_path, name, to_id, kind, hint, ctx = padded
            self._sites.setdefault(from_id, []).append(
                (file_path, name, to_id, kind, hint, ctx)
            )

    def _sites_of(self, from_id: str) -> list[tuple[str, ...]]:
        if from_id not in self._sites:
            self.prefetch([from_id])
        return self._sites.get(from_id, [])

    def _out_edges(self, from_id: str) -> dict[str, str]:
        """{target id: target name} of the CALLS edges of ``from_id``."""
        if from_id not in self._out:
            self._out[from_id] = {
                str(r.get("dst_id") or ""): str(r.get("dst_name") or "")
                for r in self.conn.find_neighbors(
                    "CALLS", src_key=from_id, return_dst=["id", "name"]
                )
                if r.get("dst_id")
            }
        return self._out[from_id]

    def _class_lineage(self, cls: str) -> frozenset[str]:
        """``cls`` and the names of its base classes, transitively."""
        if cls not in self._lineage:
            from codegraph.analysis.call_rules import class_bases

            try:
                found = set(class_bases(self.conn, {cls}))
            except Exception:
                found = set()
            self._lineage[cls] = frozenset(found | {cls})
        return self._lineage[cls]

    def _pins(self, site: tuple[str, ...], target: str) -> bool:
        """True when the shape of by-name ``site`` alone selects ``target``,
        mirroring the certain branches of call_rules.targets_for."""
        file_path, _name, _to, kind, hint, ctx = site
        t_file = _file_of(target)
        t_cls = _class_of(target)
        if kind in (BARE, MOD) and not t_cls:
            if kind == BARE and t_file == file_path:
                return True
            if hint and hint != EXTERNAL:
                return real(t_file) in hint.split("|")
            return False
        if kind == SELF and t_cls:
            own, _, bases = ctx.partition(":")
            if own and t_cls == own and t_file == file_path:
                return True
            return t_cls in {b for b in bases.split(",") if b}
        if kind == CLS and t_cls:
            known = ctx.rsplit(".", 1)[-1]
            return bool(known) and t_cls in self._class_lineage(known)
        return False

    def of(
        self,
        from_id: str,
        to_id: str,
        same: list[str] | None = None,
        name: str = "",
    ) -> str:
        """The resolution of the CALLS edge ``from_id`` -> ``to_id``;
        ``name`` is the target's name when the caller knows it."""
        sites = self._sites_of(from_id)
        if any(s[2] == to_id for s in sites):
            return RESOLVED
        if same is None:
            out = self._out_edges(from_id)
            name = out.get(to_id) or name or _name_of(to_id)
            same = [t for t, n in out.items() if n == name]
        else:
            name = name or _name_of(to_id)
        if to_id not in same:
            same = [*same, to_id]
        shared = False
        for site in sites:
            if site[1] != name or site[2]:
                continue
            pinned = [t for t in same if self._pins(site, t)]
            if to_id in pinned:
                if len(pinned) == 1:
                    return RESOLVED
                shared = True
        return AMBIGUOUS if shared or len(same) > 1 else NAME

    def of_rows(self, rows: list[dict[str, Any]]) -> list[str]:
        """The resolution of each find_neighbors CALLS row (``src_id``,
        ``dst_id`` and ``dst_name`` returned), in order. The rows hold
        every out-edge of their callers, so the same-named targets of each
        caller come from the rows themselves."""
        same: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            key = (str(r.get("src_id") or ""), str(r.get("dst_name") or ""))
            same.setdefault(key, []).append(str(r.get("dst_id") or ""))
        self.prefetch({src for src, _ in same} - {""})
        out: list[str] = []
        for r in rows:
            src = str(r.get("src_id") or "")
            dst = str(r.get("dst_id") or "")
            if not src or not dst:
                out.append(NAME)
                continue
            name = str(r.get("dst_name") or "")
            out.append(self.of(src, dst, same[(src, name)], name))
        return out
