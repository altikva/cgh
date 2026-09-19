# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Rust module declarations (mod foo;) parse to ImportRef with
#              source_module 'self::foo' and resolve to the right file,
#              respecting the 2018 module-directory rule.

from __future__ import annotations

from codegraph.imports.resolver import reset_for_tests, resolve_rust
from codegraph.parsers.rust import RustParser


class TestRustParserModDeclarations:
    def test_body_less_mod_emits_import_ref(self, tmp_path):
        reset_for_tests()
        rs = tmp_path / "lib.rs"
        rs.write_text("mod config;\n")

        result = RustParser().parse(rs)

        imports = result.imports
        assert len(imports) == 1
        assert imports[0].source_module == "self::config"

    def test_pub_mod_emits_import_ref(self, tmp_path):
        reset_for_tests()
        rs = tmp_path / "lib.rs"
        rs.write_text("pub mod util;\n")

        result = RustParser().parse(rs)

        imports = result.imports
        assert len(imports) == 1
        assert imports[0].source_module == "self::util"

    def test_inline_mod_emits_no_import_ref(self, tmp_path):
        reset_for_tests()
        rs = tmp_path / "lib.rs"
        rs.write_text("mod helpers { fn x() {} }\n")

        result = RustParser().parse(rs)

        assert len(result.imports) == 0

    def test_mod_and_use_declarations_coexist(self, tmp_path):
        reset_for_tests()
        rs = tmp_path / "lib.rs"
        rs.write_text("use std::io;\nmod config;\n")

        result = RustParser().parse(rs)

        imports = result.imports
        assert len(imports) == 2
        mod_imports = [imp for imp in imports if imp.source_module == "self::config"]
        assert len(mod_imports) == 1


class TestResolveRustModDeclarations:
    def _crate(self, tmp_path):
        reset_for_tests()
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "lib.rs").write_text("mod config;\n")
        (src / "config.rs").write_text("pub struct Config;\n")
        return tmp_path

    def test_self_config_from_lib_rs_resolves_to_config_rs(self, tmp_path):
        root = self._crate(tmp_path)

        hit = resolve_rust("self::config", root / "src" / "lib.rs", root)

        assert hit == (root / "src" / "config.rs").resolve()

    def test_self_config_resolves_to_config_mod_rs_alternate_layout(self, tmp_path):
        reset_for_tests()
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "lib.rs").write_text("mod config;\n")
        config_dir = src / "config"
        config_dir.mkdir()
        (config_dir / "mod.rs").write_text("pub struct Config;\n")

        hit = resolve_rust("self::config", src / "lib.rs", tmp_path)

        assert hit == (config_dir / "mod.rs").resolve()

    def test_module_directory_rule_from_app_rs(self, tmp_path):
        reset_for_tests()
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "lib.rs").write_text("")
        (src / "app.rs").write_text("mod handler;\n")
        app_dir = src / "app"
        app_dir.mkdir()
        (app_dir / "handler.rs").write_text("pub struct Handler;\n")
        # The decoy is the whole point: a resolver anchored on the importer's
        # own directory would sit here instead, and Rust would never look.
        (src / "handler.rs").write_text("pub struct Wrong;\n")

        hit = resolve_rust("self::handler", src / "app.rs", tmp_path)

        assert hit == (app_dir / "handler.rs").resolve()

    def test_self_from_directory_module(self, tmp_path):
        reset_for_tests()
        src = tmp_path / "src"
        src.mkdir(parents=True)
        (src / "lib.rs").write_text("mod net;\n")
        net_dir = src / "net"
        net_dir.mkdir()
        (net_dir / "mod.rs").write_text("pub mod client;\n")
        (net_dir / "client.rs").write_text("pub struct Client;\n")

        hit = resolve_rust("self::client", net_dir / "mod.rs", tmp_path)

        assert hit == (net_dir / "client.rs").resolve()

    def test_integration_test_root_treats_modules_as_siblings(self, tmp_path):
        """Cargo compiles every file directly in `tests/` as its own crate.

        So `tests/tests.rs` is a crate root and `mod util;` there means
        `tests/util.rs`. Treating it as an ordinary module file would look
        under `tests/tests/` and find nothing, which is what ripgrep's
        integration suite hits.
        """
        reset_for_tests()
        tests = tmp_path / "tests"
        tests.mkdir(parents=True)
        (tests / "tests.rs").write_text("mod util;\n")
        (tests / "util.rs").write_text("pub fn helper() {}\n")

        hit = resolve_rust("self::util", tests / "tests.rs", tmp_path)

        assert hit == (tests / "util.rs").resolve()

    def test_src_bin_entry_point_treats_modules_as_siblings(self, tmp_path):
        reset_for_tests()
        bin_dir = tmp_path / "src" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "tool.rs").write_text("mod args;\n")
        (bin_dir / "args.rs").write_text("pub struct Args;\n")

        hit = resolve_rust("self::args", bin_dir / "tool.rs", tmp_path)

        assert hit == (bin_dir / "args.rs").resolve()

    def test_resolve_returns_none_when_file_does_not_exist(self, tmp_path):
        root = self._crate(tmp_path)

        hit = resolve_rust("self::nonexistent", root / "src" / "lib.rs", root)

        assert hit is None
