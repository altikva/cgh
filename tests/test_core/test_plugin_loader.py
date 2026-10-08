# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-07-29
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Plugin loader tests. Synthetic entry
#              points exercise the five registration surfaces, the
#              [plugins] enabled/disabled config, the API version check,
#              failure isolation (import error, register() raising and
#              the rollback of what it registered, no register),
#              duplicate names, and load idempotence.

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

    def test_half_registered_plugin_is_rolled_back(self, monkeypatch):
        """An old plugin that registers its CLI, then imports a name core
        removed, must not leave the verb or a scanner behind."""

        def register(api):
            api.register_cli(lambda sub: sub.add_parser("zzz-half"))
            api.register_scanner(SimpleNamespace(name="half", deferred=False))
            api.register_extension("zzz.ns", object())
            from codegraph.guard import sync_static_rules  # noqa: F401

        def good(api):
            api.register_cli(lambda sub: sub.add_parser("zzz-good"))

        _install(
            monkeypatch,
            _entry_point("half", lambda: _module("half", register=register)),
            _entry_point("good", lambda: _module("good", register=good)),
        )
        records = plugins.load_plugins()

        assert [r.status for r in records] == ["broken", "active"]
        assert "ModuleNotFoundError" in records[0].reason
        assert [n for n, _ in plugins.cli_registrars()] == ["good"]
        assert plugins.scanners() == []
        assert plugins.get_extensions("zzz.ns") == []

    def test_import_of_removed_core_name_marks_broken(self, monkeypatch):
        def load():
            from codegraph.plugin_api import a_name_core_removed  # noqa: F401

        _install(monkeypatch, _entry_point("old", load))
        records = plugins.load_plugins()

        assert records[0].status == "broken"
        assert "import failed" in records[0].reason

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


# ---------------------------------------------------------------------------
# First-party plugins too old for this core
# ---------------------------------------------------------------------------


def _fake_distribution(site, dist: str, version: str, ep_name: str, module: str):
    """A real installed-looking distribution: a dist-info directory with
    METADATA and entry_points.txt, plus a module that records its import."""
    info = site / f"{dist.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n",
        encoding="utf-8",
    )
    (info / "entry_points.txt").write_text(
        f"[cgh]\n{ep_name} = {module}\n", encoding="utf-8"
    )
    (site / f"{module}.py").write_text(
        "import os\n"
        "CGH_PLUGIN_API = 1\n"
        f"os.environ['{module.upper()}_IMPORTED'] = '1'\n"
        "def register(api):\n"
        "    pass\n",
        encoding="utf-8",
    )


@pytest.fixture
def fake_site(tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.syspath_prepend(str(site))
    # Load from a repo with no [plugins] config, so no repo or checkout
    # setting disables the fake plugins.
    (tmp_path / "repo").mkdir()
    monkeypatch.chdir(tmp_path / "repo")
    return site


def _discovered_only(monkeypatch, *names):
    """Keep the real importlib.metadata discovery, minus whatever plugins the
    test environment has installed for real (the fake modules all start
    with zzz_, real plugins may share an entry point name)."""
    real = plugins._iter_entry_points
    monkeypatch.setattr(
        plugins,
        "_iter_entry_points",
        lambda: [
            ep for ep in real() if ep.name in names and ep.value.startswith("zzz_")
        ],
    )


class TestFirstPartyMinimums:
    @pytest.mark.parametrize(
        ("dist", "version", "needed"),
        [
            ("cgh-pii", "0.3.1", "0.4.0"),
            ("cgh-summarize", "0.2.4", "0.3.0"),
            ("cgh-classify", "0.1.3", "0.2.0"),
            ("cgh-vision", "0.5.0", "0.6.0"),
        ],
    )
    def test_below_minimum_is_refused(self, dist, version, needed):
        reason = plugins.too_old_reason(dist, version)
        assert f"{dist} {version} is too old for cgh 0.15" in reason
        assert f"needs >= {needed}" in reason
        assert 'uv tool install --force -U "cgh[plugins]"' in reason
        assert f"--with {dist}" in reason

    @pytest.mark.parametrize(
        ("dist", "version"),
        [
            ("cgh-pii", "0.4.0"),
            ("cgh-summarize", "0.3"),
            ("cgh-classify", "0.2.1"),
            ("cgh-vision", "1.0.0"),
            ("cgh-docs", "0.0.1"),  # first-party without a minimum
            ("acme-summarize", "0.0.1"),  # third-party
            ("cgh-pii", "not-a-version"),  # unknown: never guess
        ],
    )
    def test_recent_unlisted_or_unknown_is_accepted(self, dist, version):
        assert plugins.too_old_reason(dist, version) == ""

    def test_stale_distribution_is_broken_and_never_imported(
        self, fake_site, monkeypatch
    ):
        monkeypatch.delenv("ZZZ_STALE_SUMMARIZE_IMPORTED", raising=False)
        _fake_distribution(
            fake_site, "cgh-summarize", "0.2.4", "summarize", "zzz_stale_summarize"
        )
        _discovered_only(monkeypatch, "summarize")

        records = plugins.load_plugins(fake_site.parent / "repo")

        assert len(records) == 1
        rec = records[0]
        assert rec.status == "broken" and rec.too_old
        assert rec.version == "0.2.4"
        assert "cgh-summarize 0.2.4 is too old for cgh 0.15" in rec.reason
        assert "ZZZ_STALE_SUMMARIZE_IMPORTED" not in __import__("os").environ
        assert plugins.cli_registrars() == [] and plugins.scanners() == []

    def test_current_and_third_party_distributions_load(self, fake_site, monkeypatch):
        _fake_distribution(fake_site, "cgh-pii", "0.4.0", "pii", "zzz_current_pii")
        # Third-party, even with an old version and a first-party-like
        # entry point name: matched by distribution name, so untouched.
        _fake_distribution(
            fake_site, "acme-classify", "0.0.1", "classify", "zzz_acme_classify"
        )
        _discovered_only(monkeypatch, "pii", "classify")

        records = {r.name: r for r in plugins.load_plugins(fake_site.parent / "repo")}

        assert records["pii"].status == "active" and not records["pii"].too_old
        assert records["classify"].status == "active"

    def test_cgh_plugins_reports_the_stale_plugin(self, fake_site, monkeypatch, capsys):
        import json

        from codegraph.cli.commands_plugins import cmd_plugins

        _fake_distribution(fake_site, "cgh-pii", "0.3.1", "pii", "zzz_stale_pii")
        _discovered_only(monkeypatch, "pii")

        cmd_plugins(argparse.Namespace(root=str(fake_site.parent / "repo"), json=True))

        (row,) = json.loads(capsys.readouterr().out)
        assert row["status"] == "broken" and row["version"] == "0.3.1"
        assert "needs >= 0.4.0" in row["reason"]

    def test_doctor_lists_the_stale_plugin(self, fake_site, monkeypatch, tmp_path):
        from codegraph.cli.commands_monitor import _too_old_plugin_reasons

        _fake_distribution(
            fake_site, "cgh-vision", "0.5.0", "vision", "zzz_stale_vision"
        )
        _discovered_only(monkeypatch, "vision")

        (reason,) = _too_old_plugin_reasons(fake_site.parent / "repo")
        assert "cgh-vision 0.5.0 is too old" in reason

    def test_table_keeps_the_literal_extra_brackets(self, fake_site, monkeypatch):
        import io

        from rich.console import Console

        import codegraph.cli.commands_plugins as cp

        buf = io.StringIO()
        monkeypatch.setattr(cp, "console", Console(file=buf, width=400))
        _fake_distribution(fake_site, "cgh-pii", "0.3.1", "pii", "zzz_table_pii")
        _discovered_only(monkeypatch, "pii")

        cp.cmd_plugins(argparse.Namespace(root=str(fake_site.parent / "repo")))

        assert '"cgh[plugins]"' in buf.getvalue()
