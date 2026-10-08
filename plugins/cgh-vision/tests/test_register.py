# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-10-08
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: cgh-vision registration: the CLI verb only by default, the
#              image parser and deferred scanner only with
#              [plugin.vision] scan_on_index = true.

from __future__ import annotations

import pytest

pytest.importorskip("cgh_vision")

import cgh_vision

import codegraph.plugins as plugins
from codegraph.plugin_api import PluginAPI


@pytest.fixture(autouse=True)
def clean_registries():
    import codegraph.parsers as parsers

    saved = dict(parsers._REGISTRY), dict(parsers._INSTANCES)
    plugins._reset_for_tests()
    yield
    plugins._reset_for_tests()
    # register_parser writes the process-wide parser table; put it back
    # so image extensions do not leak into unrelated tests.
    parsers._REGISTRY.clear()
    parsers._REGISTRY.update(saved[0])
    parsers._INSTANCES.clear()
    parsers._INSTANCES.update(saved[1])


def _register(tmp_path, config):
    api = PluginAPI("vision", tmp_path, config, plugins._registries)
    cgh_vision.register(api)
    return api


def test_default_registers_cli_only(tmp_path):
    api = _register(tmp_path, {})
    assert api.surfaces == ["cli"]
    assert plugins._registries.scanners == []


@pytest.mark.parametrize("flag", [True, "true"])
def test_scan_on_index_registers_parser_and_scanner(tmp_path, flag):
    api = _register(tmp_path, {"scan_on_index": flag})
    assert set(api.surfaces) == {"cli", "parsers", "scanners"}
    assert [s.name for _, s in plugins._registries.scanners] == ["vision"]
