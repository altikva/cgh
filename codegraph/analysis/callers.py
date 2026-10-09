# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Callers of a function name, one row per calling function.
#              CALLS edges are name-matched, so a call can link to every
#              definition of that name (the real method and a test double);
#              the row then names those definitions instead of repeating
#              the caller once per edge. A Terraform address (var.region,
#              google_x.y, module.m) answers from the reference edges: the
#              "callers" are the blocks whose expressions use it. A
#              `Class.method` name adds the callers reaching it through an
#              interface method it implements (analysis/interfaces.py), and
#              drops the direct callers whose receiver is known to be of an
#              unrelated class (the edge was a guess by name).

from __future__ import annotations

from collections.abc import Callable


def callers_of(conn, fn_name: str) -> list[dict]:
    """Every function calling `fn_name`, as {caller, file, line}, in first-seen
    order. When the name matches more than one definition, each row also
    carries `targets`: the files of the definitions that caller links to.

    A `Class.method` name also answers for the interface methods that
    method implements (a base class, an ABC, a Protocol it conforms to): a
    call through the interface links to the interface's method, so its
    callers are listed too, each with `via` naming the interface method it
    was reached through. A caller that also calls the method directly has
    no `via`."""
    from codegraph.analysis.interfaces import (
        InterfaceResolver,
        label,
        qualified_methods,
    )
    from codegraph.analysis.terraform import users_of

    rows = [
        (
            r["src_name"],
            r["src_file_path"],
            r["src_start_line"],
            r["dst_file_path"],
            None,
        )
        for r in conn.find_neighbors(
            "CALLS",
            dst_where={"name": fn_name},
            return_src=["name", "file_path", "start_line"],
            return_dst=["file_path"],
        )
    ]
    if "." in fn_name:
        resolver = InterfaceResolver(conn)
        for fn_id in qualified_methods(conn, fn_name):
            ifaces = resolver.interfaces_of(fn_id)
            keep = _receiver_filter(conn, resolver, fn_id, ifaces)
            rows += _calls_into(conn, fn_id, None, keep=keep)
            for iface in ifaces:
                rows += _calls_into(conn, iface, label(iface), skip=fn_id, keep=keep)
        rows += [
            (
                r["src_address"],
                r["src_file_path"],
                r["src_start_line"],
                r["dst_file_path"],
                None,
            )
            for r in users_of(conn, fn_name)
        ]
    grouped: dict[tuple, dict] = {}
    vias: dict[tuple, list[str] | None] = {}
    definitions: set[str] = set()
    for name, file_path, line, target, via in rows:
        key = (name, file_path, line)
        entry = grouped.setdefault(
            key,
            {"caller": key[0], "file": key[1], "line": key[2], "targets": []},
        )
        if via is None:
            vias[key] = None
            definitions.add(target)
            if target not in entry["targets"]:
                entry["targets"].append(target)
        elif vias.setdefault(key, []) is not None and via not in vias[key]:
            vias[key].append(via)
    out = list(grouped.values())
    for entry in out:
        key = (entry["caller"], entry["file"], entry["line"])
        if len(definitions) <= 1 or not entry["targets"]:
            del entry["targets"]
        else:
            entry["targets"].sort()
        if vias.get(key):
            entry["via"] = ", ".join(sorted(vias[key]))
    return out


def _calls_into(
    conn,
    fn_id: str,
    via: str | None,
    skip: str = "",
    keep: Callable[[str], bool] | None = None,
) -> list[tuple]:
    """Callers of the Function ``fn_id`` as callers_of rows; ``skip`` drops
    one caller (the queried method, reaching its own interface), ``keep``
    the callers it rejects (by caller id)."""
    return [
        (
            r["src_name"],
            r["src_file_path"],
            r["src_start_line"],
            r["dst_file_path"],
            via,
        )
        for r in conn.find_neighbors(
            "CALLS",
            dst_key=fn_id,
            return_src=["id", "name", "file_path", "start_line"],
            return_dst=["file_path"],
        )
        if (not skip or r["src_id"] != skip) and (keep is None or keep(r["src_id"]))
    ]


def _receiver_filter(
    conn, resolver, fn_id: str, ifaces: list[str]
) -> Callable[[str], bool] | None:
    """For the method ``fn_id`` of a class, a test telling whether a caller's
    CALLS edge to it (or to an interface method it implements) can be a
    call of that class's method. A caller whose every call of the name is
    on a receiver of a known class (a ``C().f()``, a typed attribute or
    local, ``self.f()`` in class C), none of them related to the method's
    class (the class itself, its bases, its subclasses, the interfaces it
    implements), is rejected: the edge was a guess by name. A receiver of
    unknown type keeps the caller. None for a plain function."""
    from codegraph.analysis.call_rules import CLS, SELF, class_bases, subclasses
    from codegraph.analysis.interfaces import label

    owner = resolver.owner(fn_id)
    if not owner:
        return None
    cls = label(owner)
    related = set(class_bases(conn, {cls})) | subclasses(conn, {cls})
    related.update(label(resolver.owner(i)) for i in ifaces if resolver.owner(i))
    method = label(fn_id).rsplit(".", 1)[-1]
    sites: dict[str, list[tuple[str, str, str]]] = {}
    for row in conn.call_sites_into([method], [fn_id, *ifaces], ""):
        from_id, _path, _name, to_id, kind, _hint, ctx = row[:7]
        sites.setdefault(from_id, []).append((to_id, kind, ctx))

    def keep(caller: str) -> bool:
        for to_id, kind, ctx in sites.get(caller, ()):
            if to_id:
                return True  # resolved to this very function or interface
            if kind == CLS:
                known = ctx.rsplit(".", 1)[-1]
            elif kind == SELF:
                known = ctx.partition(":")[0]
            else:
                return True  # receiver of unknown type
            if not known or known in related:
                return True
        # No site at all: an edge from a parser without call shapes.
        return not sites.get(caller)

    return keep
