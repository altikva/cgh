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
#              interface method it implements (analysis/interfaces.py).

from __future__ import annotations


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
            rows += _calls_into(conn, fn_id, None)
            for iface in resolver.interfaces_of(fn_id):
                rows += _calls_into(conn, iface, label(iface), skip=fn_id)
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


def _calls_into(conn, fn_id: str, via: str | None, skip: str = "") -> list[tuple]:
    """Callers of the Function ``fn_id`` as callers_of rows; ``skip`` drops
    one caller (the queried method, reaching its own interface)."""
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
        if not skip or r["src_id"] != skip
    ]
