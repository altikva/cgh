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
#              "callers" are the blocks whose expressions use it.

from __future__ import annotations


def callers_of(conn, fn_name: str) -> list[dict]:
    """Every function calling `fn_name`, as {caller, file, line}, in first-seen
    order. When the name matches more than one definition, each row also
    carries `targets`: the files of the definitions that caller links to."""
    from codegraph.analysis.terraform import users_of

    rows = [
        (r["src_name"], r["src_file_path"], r["src_start_line"], r["dst_file_path"])
        for r in conn.find_neighbors(
            "CALLS",
            dst_where={"name": fn_name},
            return_src=["name", "file_path", "start_line"],
            return_dst=["file_path"],
        )
    ]
    if "." in fn_name:
        rows += [
            (
                r["src_address"],
                r["src_file_path"],
                r["src_start_line"],
                r["dst_file_path"],
            )
            for r in users_of(conn, fn_name)
        ]
    grouped: dict[tuple, dict] = {}
    definitions: set[str] = set()
    for name, file_path, line, target in rows:
        key = (name, file_path, line)
        entry = grouped.setdefault(
            key,
            {"caller": key[0], "file": key[1], "line": key[2], "targets": []},
        )
        definitions.add(target)
        if target not in entry["targets"]:
            entry["targets"].append(target)
    out = list(grouped.values())
    for entry in out:
        if len(definitions) <= 1:
            del entry["targets"]
        else:
            entry["targets"].sort()
    return out
