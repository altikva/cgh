"""
Tests for tsconfig.json path alias resolution.

The TS resolver layers compilerOptions.paths over the filesystem fallback
so imports like ``"@/utils"`` (configured in tsconfig) wire IMPORTS edges
to ``src/utils.ts``. Falls back to filesystem-only behaviour when no
tsconfig is reachable.
"""

from __future__ import annotations

import textwrap

import pytest

from codegraph.imports.resolver import resolve_js_ts
from codegraph.imports.tsconfig import _clear_cache, _read_tsconfig, _strip_jsonc, load_aliases, resolve_alias


@pytest.fixture(autouse=True)
def clear_alias_cache():
    _clear_cache()
    yield
    _clear_cache()


class TestStripJsonc:
    def test_line_comments_stripped(self):
        text = '{"a": 1} // trailing comment\n'
        assert "// trailing" not in _strip_jsonc(text)

    def test_block_comments_stripped(self):
        text = '{"a": /* inline */ 1}'
        out = _strip_jsonc(text)
        assert "inline" not in out
        assert '{"a":  1}' in out

    def test_trailing_commas_stripped(self):
        text = '{"a": 1, "b": 2,}'
        assert _strip_jsonc(text) == '{"a": 1, "b": 2}'

    def test_comment_inside_string_preserved(self):
        text = '{"url": "https://example.com/path"}'
        # // inside a string is real content
        assert "https://example.com/path" in _strip_jsonc(text)


class TestReadTsconfig:
    def test_simple(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text(
            textwrap.dedent("""\
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {
                        "@/*": ["src/*"]
                    }
                }
            }
            """)
        )
        cfg = _read_tsconfig(tmp_path / "tsconfig.json")
        assert "paths" in cfg
        assert cfg["paths"]["@/*"] == ["src/*"]

    def test_extends_chain(self, tmp_path):
        (tmp_path / "tsconfig.base.json").write_text(
            textwrap.dedent("""\
            {
                "compilerOptions": {
                    "baseUrl": ".",
                    "paths": {
                        "@base/*": ["base/*"]
                    }
                }
            }
            """)
        )
        (tmp_path / "tsconfig.json").write_text(
            textwrap.dedent("""\
            {
                "extends": "./tsconfig.base",
                "compilerOptions": {
                    "paths": {
                        "@app/*": ["app/*"]
                    }
                }
            }
            """)
        )
        cfg = _read_tsconfig(tmp_path / "tsconfig.json")
        # Both base + child aliases present
        assert "@base/*" in cfg["paths"]
        assert "@app/*" in cfg["paths"]

    def test_extends_cycle_safe(self, tmp_path):
        (tmp_path / "a.json").write_text('{"extends": "./b"}')
        (tmp_path / "b.json").write_text('{"extends": "./a"}')
        # Should not hang or raise — returns whatever it can
        assert _read_tsconfig(tmp_path / "a.json") is not None


class TestLoadAliases:
    def test_finds_nearest_tsconfig(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}'
        )
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        cfg = load_aliases(sub)
        assert "@/*" in cfg.get("paths", {})

    def test_no_tsconfig_returns_empty(self, tmp_path):
        cfg = load_aliases(tmp_path)
        assert cfg == {}

    def test_memoized_per_directory(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}'
        )
        first = load_aliases(tmp_path)
        # mutate the file
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"paths": {"@/*": ["NEW/*"]}}}'
        )
        # second call should see the cached value
        second = load_aliases(tmp_path)
        assert first == second


class TestResolveAlias:
    def test_glob_alias(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
        )
        out = resolve_alias("@/utils", tmp_path)
        assert any(p.name == "utils" and "src" in str(p) for p in out)

    def test_exact_alias(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@core": ["src/core/index"]}}}'
        )
        out = resolve_alias("@core", tmp_path)
        assert any("core" in str(p) for p in out)

    def test_no_match_returns_empty(self, tmp_path):
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
        )
        out = resolve_alias("react", tmp_path)
        assert out == []


class TestResolveJsTsWithAlias:
    def test_alias_resolves_to_real_file(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "utils.ts").write_text("export const x = 1;\n")
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
        )
        importer = tmp_path / "main.ts"
        importer.write_text("")
        result = resolve_js_ts("@/utils", importer, tmp_path)
        assert result is not None
        assert result.name == "utils.ts"

    def test_alias_to_index_file(self, tmp_path):
        comp = tmp_path / "src" / "components"
        comp.mkdir(parents=True)
        (comp / "index.tsx").write_text("export const X = 1;\n")
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
        )
        importer = tmp_path / "main.ts"
        importer.write_text("")
        result = resolve_js_ts("@/components", importer, tmp_path)
        assert result is not None
        assert result.name == "index.tsx"

    def test_no_tsconfig_bare_specifier_returns_none(self, tmp_path):
        importer = tmp_path / "main.ts"
        importer.write_text("")
        assert resolve_js_ts("@/utils", importer, tmp_path) is None

    def test_alias_not_matching_falls_through(self, tmp_path):
        """A tsconfig exists but the import doesn't match any pattern,
        so the resolver returns None (no edge for a third-party module)."""
        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}'
        )
        importer = tmp_path / "main.ts"
        importer.write_text("")
        assert resolve_js_ts("react", importer, tmp_path) is None
