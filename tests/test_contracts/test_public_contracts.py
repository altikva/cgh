# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Contract snapshots of every surface other code depends on:
#              the MIT-licensed SDK, the plugin API and its registration
#              hooks, the MCP tool names and input schemas, and the CLI
#              verbs with their options. A change to any of them must be
#              deliberate, so it shows up here as a diff to review.

"""Contract snapshots.

The JSON files under ``tests/test_contracts/snapshots/`` are the reviewed
contracts. When a change is intended, regenerate them and commit the diff:

    CGH_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contracts -q

Run it with no plugin installed (core only); plugin CLI verbs and MCP tools
are not part of these contracts.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import difflib
import inspect
import json
import os
import re
from pathlib import Path

import pytest

SNAPSHOTS = Path(__file__).parent / "snapshots"
REPO = Path(__file__).resolve().parents[2]
UPDATE_CMD = "CGH_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_contracts -q"


def check_snapshot(name: str, actual: object) -> None:
    """Compare ``actual`` to snapshots/<name>.json, or rewrite it when
    CGH_UPDATE_SNAPSHOTS=1. A mismatch fails with a unified diff."""
    path = SNAPSHOTS / f"{name}.json"
    text = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    if os.environ.get("CGH_UPDATE_SNAPSHOTS") == "1":
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(f"no snapshot {path.name}; create it with:\n  {UPDATE_CMD}")
    expected = path.read_text(encoding="utf-8")
    if expected == text:
        return
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"snapshots/{path.name} (reviewed)",
            tofile="current code",
        )
    )
    pytest.fail(
        f"the {name} contract changed:\n{diff}\n"
        f"If the change is deliberate, regenerate and commit the snapshot:\n"
        f"  {UPDATE_CMD}",
        pytrace=False,
    )


# ---------------------------------------------------------------------------
# Version-stable descriptions of Python objects
# ---------------------------------------------------------------------------


def _annotation(ann: object) -> str:
    if ann is inspect.Parameter.empty:
        return ""
    if isinstance(ann, str):
        return ann
    return getattr(ann, "__qualname__", None) or repr(ann)


def describe_signature(obj) -> list[dict] | None:
    """Parameters as plain dicts: stable across Python versions, unlike
    str(inspect.signature(...)), whose quoting of annotations changes."""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    params = []
    for p in sig.parameters.values():
        entry = {"name": p.name, "kind": p.kind.name}
        if p.annotation is not inspect.Parameter.empty:
            entry["annotation"] = _annotation(p.annotation)
        if p.default is not inspect.Parameter.empty:
            entry["default"] = repr(p.default)
        params.append(entry)
    out: list[dict] = params
    if sig.return_annotation is not inspect.Signature.empty:
        out = [*params, {"returns": _annotation(sig.return_annotation)}]
    return out


def _own_init(cls: type) -> bool:
    return any("__init__" in vars(c) for c in cls.__mro__ if c.__module__ != "builtins")


def describe(obj) -> dict:
    if inspect.isclass(obj):
        entry: dict = {"kind": "class"}
        if dataclasses.is_dataclass(obj):
            entry["fields"] = [
                {"name": f.name, "type": _annotation(f.type)}
                for f in dataclasses.fields(obj)
            ]
        elif _own_init(obj):
            entry["init"] = describe_signature(obj.__init__)
        if issubclass(obj, BaseException):
            entry["bases"] = [b.__name__ for b in obj.__bases__]
        methods = {}
        for name, member in vars(obj).items():
            if name.startswith("_"):
                continue
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if inspect.isfunction(member):
                methods[name] = describe_signature(member)
            elif isinstance(member, property):
                methods[name] = "property"
        if methods:
            entry["methods"] = methods
        return entry
    if callable(obj):
        return {"kind": "function", "signature": describe_signature(obj)}
    return {"kind": "constant", "value": repr(obj)}


# ---------------------------------------------------------------------------
# SDK (MIT surface: widening or changing it is a licensing decision)
# ---------------------------------------------------------------------------


def test_sdk_contract():
    import codegraph.sdk as sdk

    check_snapshot(
        "sdk",
        {
            "__all__": sorted(sdk.__all__),
            "exports": {name: describe(getattr(sdk, name)) for name in sdk.__all__},
        },
    )


# ---------------------------------------------------------------------------
# Plugin API
# ---------------------------------------------------------------------------


def _consumed_extension_namespaces() -> list[str]:
    """Extension namespaces core reads (get_extensions / extension_entries)."""
    pattern = re.compile(r"""(?:get_extensions|extension_entries)\(\s*["']([^"']+)""")
    found: set[str] = set()
    for src in (REPO / "codegraph").rglob("*.py"):
        found.update(pattern.findall(src.read_text(encoding="utf-8")))
    return sorted(found)


def test_plugin_api_contract():
    import codegraph.plugin_api as api
    import codegraph.plugins as plugins

    public = sorted(n for n in dir(api) if not n.startswith("_"))
    # Typing helpers imported into the module are not part of the surface.
    imported = {"Any", "Callable", "Path", "Protocol", "TYPE_CHECKING"}
    imported |= {"annotations", "dataclass", "field", "runtime_checkable"}
    names = [n for n in public if n not in imported]
    check_snapshot(
        "plugin_api",
        {
            "API_VERSION": api.API_VERSION,
            "entry_point_group": plugins._ENTRY_POINT_GROUP,
            "plugin_module_hooks": {
                "register": "module-level callable register(api: PluginAPI)",
                "CGH_PLUGIN_API": "optional int, must equal API_VERSION",
            },
            "registration_methods": {
                name: describe_signature(member)
                for name, member in vars(api.PluginAPI).items()
                if not name.startswith("_") and inspect.isfunction(member)
            },
            "consumed_extension_namespaces": _consumed_extension_namespaces(),
            "names": {name: describe(getattr(api, name)) for name in names},
            "core_plugin_readers": {
                name: describe_signature(getattr(plugins, name))
                for name in (
                    "cli_registrars",
                    "mcp_registrars",
                    "scanners",
                    "get_extensions",
                    "extension_entries",
                    "loaded_plugins",
                    "load_plugins",
                )
            },
        },
    )


def test_plugin_module_hook_names_match_the_loader():
    """The hook names recorded in the snapshot are the ones the loader
    actually looks up; renaming either side breaks every plugin."""
    src = (REPO / "codegraph" / "plugins.py").read_text(encoding="utf-8")
    assert 'getattr(module, "CGH_PLUGIN_API"' in src
    assert re.search(r"""getattr\(\s*module,\s*["']register["']""", src)


# ---------------------------------------------------------------------------
# MCP tools: names + input schemas (descriptions excluded on purpose)
# ---------------------------------------------------------------------------


def _schema_type(prop: dict) -> object:
    if "type" in prop:
        t = prop["type"]
        if t == "array" and isinstance(prop.get("items"), dict):
            return f"array[{_schema_type(prop['items'])}]"
        return t
    for key in ("anyOf", "oneOf"):
        if key in prop:
            return sorted(str(_schema_type(p)) for p in prop[key])
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    return "any"


def _normalize_schema(schema: dict) -> dict:
    props = schema.get("properties", {})
    return {
        "properties": {
            name: {
                "type": _schema_type(prop),
                **({"default": prop["default"]} if "default" in prop else {}),
            }
            for name, prop in props.items()
        },
        "required": sorted(schema.get("required", [])),
    }


def _mcp_tools() -> dict[str, dict]:
    import codegraph.server as srv

    if hasattr(srv.mcp, "list_tools"):
        tools = {t.name: t for t in asyncio.run(srv.mcp.list_tools())}
    else:  # fastmcp 2.x
        tools = dict(asyncio.run(srv.mcp.get_tools()))
    return {name: _normalize_schema(tool.parameters) for name, tool in tools.items()}


def test_mcp_tools_contract():
    check_snapshot("mcp_tools", _mcp_tools())


# ---------------------------------------------------------------------------
# CLI: verbs and option strings (core only, plugins not loaded)
# ---------------------------------------------------------------------------


def _describe_parser(parser: argparse.ArgumentParser) -> dict:
    options: list[str] = []
    positionals: list[str] = []
    verbs: dict[str, dict] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                verbs[name] = _describe_parser(sub)
        elif action.option_strings:
            options.extend(action.option_strings)
        else:
            positionals.append(action.dest)
    out: dict = {"options": sorted(options)}
    if positionals:
        out["positionals"] = positionals
    if verbs:
        out["verbs"] = verbs
    return out


def _cli_tree() -> dict:
    import codegraph.__main__ as cli

    ap = cli._LogoArgumentParser(prog="codegraph", add_help=False)
    cli._add_root(ap)
    ap.add_argument("--version", action="store_true")
    ap.add_argument("-h", "--help", action="store_true")
    sub = ap.add_subparsers(dest="cmd", parser_class=cli._LogoArgumentParser)
    cli._register_setup_and_serve(sub)
    cli._register_inspect(sub)
    cli._register_analysis(sub)
    cli._register_state_and_hooks(sub)
    return _describe_parser(ap)


def test_cli_contract():
    tree = _cli_tree()
    check_snapshot(
        "cli", {"top_level_verbs": sorted(tree.get("verbs", {})), "tree": tree}
    )


def test_cli_registration_mirrors_main():
    """The test rebuilds the parser the way main() does; if main() grows a
    new _register_* call, this fails so the snapshot keeps covering it."""
    import codegraph.__main__ as cli

    body = inspect.getsource(cli.main)
    called = sorted(set(re.findall(r"\b(_register_\w+)\(sub\)", body)))
    assert called == [
        "_register_analysis",
        "_register_inspect",
        "_register_setup_and_serve",
        "_register_state_and_hooks",
    ]
