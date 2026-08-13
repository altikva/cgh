# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-08-13
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: `cgh examples` verb: list runnable examples bundled INSIDE
#              the installed packages (base cgh + each plugin) and install
#              one locally to modify. Examples ship as package data, so this
#              works with no git checkout and no network. Discovery walks the
#              base `codegraph/examples/` and every cgh plugin's
#              `<package>/examples/` via the `cgh` entry-point group. Each
#              example is a directory; its description is the first prose
#              line of its README.md.

from __future__ import annotations

import argparse
from importlib import resources
from importlib.metadata import entry_points

from codegraph.cli import console


def _example_packages() -> list[str]:
    """The base package plus every installed cgh plugin's import package.
    Deduplicated, order-stable (base first)."""
    pkgs = ["codegraph"]
    try:
        for ep in entry_points(group="cgh"):
            mod = ep.value.split(":", 1)[0].split(".", 1)[0]
            if mod and mod not in pkgs:
                pkgs.append(mod)
    except Exception:
        pass
    return pkgs


def _description(readme_text: str) -> str:
    """The first prose line of the README, skipping the `# Title` heading,
    so the listing shows what the example does, not its name again."""
    for line in readme_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        return s
    return ""


def discover_examples() -> list[dict]:
    """Every bundled example as {name, description, package}. A name can
    exist in more than one package; the package disambiguates it."""
    out: list[dict] = []
    for pkg in _example_packages():
        try:
            root = resources.files(pkg) / "examples"
        except (ModuleNotFoundError, TypeError):
            continue
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir(), key=lambda e: e.name):
            if not entry.is_dir():
                continue
            desc = ""
            readme = entry / "README.md"
            if readme.is_file():
                try:
                    desc = _description(readme.read_text(encoding="utf-8"))
                except OSError:
                    pass
            out.append({"name": entry.name, "description": desc, "package": pkg})
    return out


def _copy_tree(src, dest_dir) -> int:
    """Copy a Traversable directory (which may live inside a wheel/zip) to a
    real filesystem directory. Returns the number of files written."""
    import os

    n = 0
    for entry in src.iterdir():
        target = os.path.join(dest_dir, entry.name)
        if entry.is_dir():
            os.makedirs(target, exist_ok=True)
            n += _copy_tree(entry, target)
        else:
            with open(target, "wb") as fh:
                fh.write(entry.read_bytes())
            n += 1
    return n


def cmd_examples(args: argparse.Namespace) -> None:
    if args.example_action == "install":
        _install(args)
    else:
        _list()


def _list() -> None:
    found = discover_examples()
    if not found:
        console.print("[dim]no bundled examples found.[/dim]")
        return
    width = max(len(e["name"]) for e in found)
    for e in found:
        src = "" if e["package"] == "codegraph" else f" [dim]({e['package']})[/dim]"
        console.print(f"  [cyan]{e['name']:<{width}}[/cyan]  {e['description']}{src}")
    console.print(
        "\n[dim]install one with: cgh examples install <name> [--dest DIR][/dim]"
    )


def _install(args: argparse.Namespace) -> None:
    import os

    name = args.name
    if not name:
        console.print("[red]usage: cgh examples install <name> [--dest DIR][/red]")
        raise SystemExit(2)
    matches = [e for e in discover_examples() if e["name"] == name]
    if not matches:
        console.print(f"[red]no example named {name!r}.[/red] Try: cgh examples")
        raise SystemExit(2)
    if len(matches) > 1 and not args.package:
        pkgs = ", ".join(m["package"] for m in matches)
        console.print(
            f"[yellow]{name!r} exists in several packages ({pkgs}); "
            "pass --package to pick one.[/yellow]"
        )
        raise SystemExit(2)
    chosen = next(
        (m for m in matches if not args.package or m["package"] == args.package),
        None,
    )
    if chosen is None:
        console.print(f"[red]{name!r} not found in package {args.package!r}.[/red]")
        raise SystemExit(2)

    src = resources.files(chosen["package"]) / "examples" / name
    dest = os.path.join(os.path.abspath(args.dest), name)
    if os.path.exists(dest) and not args.force:
        console.print(
            f"[yellow]{dest} already exists; pass --force to overwrite.[/yellow]"
        )
        raise SystemExit(2)
    os.makedirs(dest, exist_ok=True)
    count = _copy_tree(src, dest)
    console.print(f"[green]+[/green] installed {name} ({count} file(s)) to {dest}")
    console.print(f"[dim]open {os.path.join(name, 'README.md')} to get started.[/dim]")


def register_examples_parser(sub) -> None:
    p = sub.add_parser(
        "examples",
        help="List bundled examples, or install one locally (no git, no network)",
    )
    p.add_argument(
        "example_action",
        nargs="?",
        default="list",
        choices=["list", "install"],
        help="list (default) or install",
    )
    p.add_argument("name", nargs="?", default="", help="Example name to install")
    p.add_argument("--dest", default=".", help="Directory to install into (default: .)")
    p.add_argument("--package", default="", help="Disambiguate a name across packages")
    p.add_argument(
        "--force", action="store_true", help="Overwrite an existing destination"
    )
    p.set_defaults(func=cmd_examples)
