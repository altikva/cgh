# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the config-as-data parsers (JSON / TOML / YAML).
#              Verifies top-level keys + one nested level become sections, and
#              that malformed files degrade without raising.

from __future__ import annotations

from codegraph.parsers import get_parser, is_supported
from codegraph.parsers.base import FileIndex


def _titles(idx):
    return {s.title for s in idx.sections}


class TestJson:
    def test_package_json_sections(self, tmp_path):
        f = tmp_path / "package.json"
        f.write_text(
            "{\n"
            '  "name": "demo",\n'
            '  "scripts": {\n'
            '    "build": "tsc",\n'
            '    "test": "vitest"\n'
            "  },\n"
            '  "dependencies": {\n'
            '    "vue": "^3.0.0"\n'
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        idx = get_parser(".json").parse(f)
        assert isinstance(idx, FileIndex)
        titles = _titles(idx)
        # top-level keys
        assert "name" in titles
        assert "scripts" in titles
        assert "dependencies" in titles
        # one nested level
        assert "scripts.build" in titles
        assert "scripts.test" in titles
        assert "dependencies.vue" in titles

    def test_malformed_json_no_raise(self, tmp_path):
        f = tmp_path / "broken.json"
        f.write_text('{ "a": ', encoding="utf-8")
        idx = get_parser(".json").parse(f)
        assert isinstance(idx, FileIndex)
        assert idx.sections == []


class TestToml:
    def test_pyproject_tables(self, tmp_path):
        f = tmp_path / "pyproject.toml"
        f.write_text(
            "[project]\n"
            'name = "cgh"\n'
            'version = "0.4.0"\n'
            "\n"
            "[tool.ruff]\n"
            "line-length = 100\n",
            encoding="utf-8",
        )
        idx = get_parser(".toml").parse(f)
        titles = _titles(idx)
        assert "project" in titles
        assert "tool" in titles

    def test_malformed_toml_falls_back(self, tmp_path):
        f = tmp_path / "bad.toml"
        f.write_text("[project\nname = oops", encoding="utf-8")
        idx = get_parser(".toml").parse(f)
        assert isinstance(idx, FileIndex)
        # bracket scan still surfaces the header text
        assert any("project" in s.title for s in idx.sections)


class TestYaml:
    def test_github_actions_jobs(self, tmp_path):
        f = tmp_path / "ci.yml"
        f.write_text(
            "name: CI\n"
            "on:\n"
            "  push:\n"
            "    branches: [main]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo build\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo test\n",
            encoding="utf-8",
        )
        idx = get_parser(".yaml").parse(f)
        titles = _titles(idx)
        assert "name" in titles
        assert "jobs" in titles
        # one section per job (nested level)
        assert "jobs.build" in titles
        assert "jobs.test" in titles

    def test_k8s_kind_name_section(self, tmp_path):
        f = tmp_path / "deploy.yaml"
        f.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: web\n"
            "spec:\n"
            "  replicas: 3\n",
            encoding="utf-8",
        )
        idx = get_parser(".yaml").parse(f)
        titles = _titles(idx)
        assert "Deployment/web" in titles

    def test_malformed_yaml_no_raise(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("foo: [unclosed\n  bar: : :\n", encoding="utf-8")
        idx = get_parser(".yaml").parse(f)
        assert isinstance(idx, FileIndex)


def test_extensions_supported():
    for ext in (".json", ".yaml", ".yml", ".toml"):
        assert is_supported(f"x{ext}")
