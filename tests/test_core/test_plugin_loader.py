# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Plugin loader tests. Synthetic entry
#              points exercise the five registration surfaces, the
#              [plugins] enabled/disabled config, the API version check,
#              failure isolation (import error, register() raising, no
#              register), duplicate names, and load idempotence.

from __future__ import annotations

import argparse
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import codegraph.plugins as plugins
from codegraph.plugin_api import API_VERSION


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """Reset loader state around every test and clean parser pollution."""
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()
    import codegraph.parsers as parsers

    parsers._REGISTRY.pop(".zzztest", None)
    parsers._INSTANCES.pop(".zzztest", None)


def _module(name: str, api_version=API_VERSION, register=None) -> types.ModuleType:
    mod = types.ModuleType(name)
    if api_version is not None:
        mod.CGH_PLUGIN_API = api_version
    if register is not None:
        mod.register = register
    return mod


def _entry_point(name: str, loader) -> SimpleNamespace:
    return SimpleNamespace(name=name, load=loader)


def _install(monkeypatch, *entry_points) -> None:
    monkeypatch.setattr(plugins, "_iter_entry_points", lambda: list(entry_points))


class TestSurfaces:
    def test_parser_registration_reaches_the_registry(self, monkeypatch):
        def register(api):
            from codegraph.parsers.base import BaseParser, FileIndex

            @api.register_parser(".zzztest")
            class ZzzParser(BaseParser):
                lang = "zzztest"
                extensions = [".zzztest"]

                def parse(self, path: Path) -> FileIndex:
                    return FileIndex(path=str(path), lang=self.lang)

        _install(
            monkeypatch, _entry_point("zzz", lambda: _module("zzz", register=register))
        )
        records = plugins.load_plugins()

        assert records[0].status == "active"
        assert "parsers" in records[0].surfaces
        from codegraph.parsers import get_parser, get_supported_extensions

        assert ".zzztest" in get_supported_extensions()
        assert get_parser(".zzztest") is not None

    def test_cli_registrar_adds_a_dispatchable_command(self, monkeypatch):
        calls = []

        def register(api):
            def add_cli(sub):
                p = sub.add_parser("zzz-hello")
                p.set_defaults(func=lambda args: calls.append("ran"))

            api.register_cli(add_cli)

        _install(
            monkeypatch, _entry_point("zzz", lambda: _module("zzz", register=register))
        )
        plugins.load_plugins()

        ap = argparse.ArgumentParser()
        sub = ap.add_subparsers(dest="cmd")
        for _name, registrar in plugins.cli_registrars():
            registrar(sub)
        args = ap.parse_args(["zzz-hello"])
        args.func(args)
        assert calls == ["ran"]

    def test_extensions_registry(self, monkeypatch):
        backend = object()

        def register(api):
            api.register_extension("summarize.backend", backend)
            assert api.get_extensions("summarize.backend") == [backend]

        _install(
            monkeypatch, _entry_point("zzz", lambda: _module("zzz", register=register))
        )
        records = plugins.load_plugins()

        assert records[0].status == "active"
        assert plugins.get_extensions("summarize.backend") == [backend]
        assert plugins.get_extensions("unknown.namespace") == []

    def test_scanner_and_mcp_registration_are_recorded(self, monkeypatch):
        def register(api):
            api.register_scanner(
                SimpleNamespace(name="s", deferred=False, scan=lambda *a: [])
            )
            api.register_mcp_tools(lambda mcp: None)

        _install(
            monkeypatch, _entry_point("zzz", lambda: _module("zzz", register=register))
        )
        records = plugins.load_plugins()

        assert set(records[0].surfaces) == {"scanners", "mcp"}
        assert len(plugins.scanners()) == 1
        assert len(plugins.mcp_registrars()) == 1


class TestFailureIsolation:
    def test_import_error_marks_broken(self, monkeypatch):
        def boom():
            raise ImportError("missing native dep")

        _install(monkeypatch, _entry_point("bad", boom))
        records = plugins.load_plugins()

        assert records[0].status == "broken"
        assert "import failed" in records[0].reason

    def test_register_raising_marks_broken(self, monkeypatch):
        def register(api):
            raise RuntimeError("bug in plugin")

        _install(
            monkeypatch, _entry_point("bad", lambda: _module("bad", register=register))
        )
        records = plugins.load_plugins()

        assert records[0].status == "broken"
        assert "register() raised" in records[0].reason

    def test_missing_register_marks_broken(self, monkeypatch):
        _install(monkeypatch, _entry_point("bad", lambda: _module("bad")))
        records = plugins.load_plugins()

        assert records[0].status == "broken"
        assert "no callable register" in records[0].reason

    def test_wrong_api_version_marks_incompatible(self, monkeypatch):
        _install(
            monkeypatch,
            _entry_point(
                "old",
                lambda: _module("old", api_version=99, register=lambda api: None),
            ),
        )
        records = plugins.load_plugins()

        assert records[0].status == "incompatible"
        assert records[0].api_version == 99

    def test_one_broken_plugin_does_not_stop_the_next(self, monkeypatch):
        seen = []

        def boom():
            raise ImportError("nope")

        def register(api):
            seen.append(api.plugin_name)

        _install(
            monkeypatch,
            _entry_point("bad", boom),
            _entry_point("good", lambda: _module("good", register=register)),
        )
        records = plugins.load_plugins()

        assert [r.status for r in records] == ["broken", "active"]
        assert seen == ["good"]

    def test_duplicate_name_is_flagged(self, monkeypatch):
        def register(api):
            pass

        _install(
            monkeypatch,
            _entry_point("twin", lambda: _module("twin1", register=register)),
            _entry_point("twin", lambda: _module("twin2", register=register)),
        )
        records = plugins.load_plugins()

        statuses = sorted(r.status for r in records)
        assert statuses == ["active", "duplicate"]


class TestConfigGating:
    def _repo(self, tmp_path: Path, body: str) -> Path:
        cg = tmp_path / ".codegraph"
        cg.mkdir()
        (cg / "config.toml").write_text(body, encoding="utf-8")
        return tmp_path

    def test_disabled_list_skips_register(self, tmp_path, monkeypatch):
        root = self._repo(tmp_path, '[plugins]\ndisabled = ["zzz"]\n')
        called = []
        _install(
            monkeypatch,
            _entry_point(
                "zzz",
                lambda: _module("zzz", register=lambda api: called.append(1)),
            ),
        )
        records = plugins.load_plugins(root)

        assert records[0].status == "disabled"
        assert called == []

    def test_allowlist_mode(self, tmp_path, monkeypatch):
        root = self._repo(tmp_path, '[plugins]\nenabled = ["a"]\n')

        def register(api):
            pass

        _install(
            monkeypatch,
            _entry_point("a", lambda: _module("a", register=register)),
            _entry_point("b", lambda: _module("b", register=register)),
        )
        records = {r.name: r for r in plugins.load_plugins(root)}

        assert records["a"].status == "active"
        assert records["b"].status == "disabled"

    def test_plugin_table_reaches_the_api(self, tmp_path, monkeypatch):
        root = self._repo(tmp_path, "[plugin.zzz]\nner = false\nlevel = 3\n")
        seen = {}

        def register(api):
            seen.update(api.config)

        _install(
            monkeypatch, _entry_point("zzz", lambda: _module("zzz", register=register))
        )
        plugins.load_plugins(root)

        assert seen == {"ner": False, "level": 3}


class TestIdempotence:
    def test_second_load_is_a_noop(self, monkeypatch):
        count = []

        def register(api):
            count.append(1)

        _install(
            monkeypatch, _entry_point("zzz", lambda: _module("zzz", register=register))
        )
        first = plugins.load_plugins()
        second = plugins.load_plugins()

        assert count == [1]
        assert [r.name for r in first] == [r.name for r in second]
        assert plugins.loaded_plugins()[0].status == "active"
