# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-09-19
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Go addresses a package directory through the module path in
#              go.mod, Rust addresses a module through a use path. Both
#              parsed their imports and resolved none, so their file graphs
#              had nodes and no edges.

from __future__ import annotations

from codegraph.imports.resolver import reset_for_tests, resolve_go, resolve_rust


class TestResolveGo:
    def _module(self, tmp_path, module="github.com/acme/svc"):
        reset_for_tests()
        (tmp_path / "go.mod").write_text(f"module {module}\n\ngo 1.22\n")
        db = tmp_path / "internal" / "db"
        db.mkdir(parents=True)
        (db / "db.go").write_text("package db\n")
        (db / "db_test.go").write_text("package db\n")
        return tmp_path

    def test_resolves_a_package_of_this_module(self, tmp_path):
        root = self._module(tmp_path)
        importer = root / "main.go"

        hit = resolve_go("github.com/acme/svc/internal/db", importer, root)

        assert hit == (root / "internal" / "db" / "db.go").resolve()

    def test_prefers_the_file_named_after_the_package(self, tmp_path):
        root = self._module(tmp_path)
        (root / "internal" / "db" / "aaa.go").write_text("package db\n")

        hit = resolve_go("github.com/acme/svc/internal/db", root / "main.go", root)

        assert hit.name == "db.go", "aaa.go sorts first but db.go names the package"

    def test_a_package_of_only_tests_resolves_to_nothing(self, tmp_path):
        root = self._module(tmp_path)
        only = root / "internal" / "probe"
        only.mkdir()
        (only / "probe_test.go").write_text("package probe\n")

        assert (
            resolve_go("github.com/acme/svc/internal/probe", root / "m.go", root)
            is None
        )

    def test_the_standard_library_stays_unresolved(self, tmp_path):
        root = self._module(tmp_path)

        assert resolve_go("fmt", root / "main.go", root) is None
        assert resolve_go("encoding/json", root / "main.go", root) is None

    def test_a_third_party_module_stays_unresolved(self, tmp_path):
        root = self._module(tmp_path)

        assert resolve_go("github.com/other/lib/x", root / "main.go", root) is None

    def test_no_go_mod_means_no_edge(self, tmp_path):
        """Without a module path there is nothing to anchor an import on."""
        reset_for_tests()
        (tmp_path / "main.go").write_text("package main\n")

        assert (
            resolve_go("github.com/acme/svc/db", tmp_path / "main.go", tmp_path) is None
        )


class TestResolveRust:
    def _crate(self, tmp_path):
        reset_for_tests()
        src = tmp_path / "src"
        (src / "net").mkdir(parents=True)
        (src / "main.rs").write_text("fn main() {}\n")
        (src / "config.rs").write_text("pub struct C;\n")
        (src / "net" / "mod.rs").write_text("pub mod client;\n")
        (src / "net" / "client.rs").write_text("pub struct Client;\n")
        return tmp_path

    def test_crate_path_starts_at_the_crate_root(self, tmp_path):
        root = self._crate(tmp_path)

        hit = resolve_rust("crate::config", root / "src" / "main.rs", root)

        assert hit == (root / "src" / "config.rs").resolve()

    def test_a_directory_module_resolves_through_mod_rs(self, tmp_path):
        root = self._crate(tmp_path)

        hit = resolve_rust("crate::net", root / "src" / "main.rs", root)

        assert hit == (root / "src" / "net" / "mod.rs").resolve()

    def test_a_grouped_import_is_cut_back_to_its_path(self, tmp_path):
        """The parser hands back the raw use tree, braces included."""
        root = self._crate(tmp_path)

        hit = resolve_rust(
            "crate::net::{client, other}", root / "src" / "main.rs", root
        )

        assert hit == (root / "src" / "net" / "mod.rs").resolve()

    def test_an_alias_is_cut_back_to_its_path(self, tmp_path):
        root = self._crate(tmp_path)

        hit = resolve_rust("crate::config as cfg", root / "src" / "main.rs", root)

        assert hit == (root / "src" / "config.rs").resolve()

    def test_super_walks_up_to_the_parent_module(self, tmp_path):
        """`super` is one module up, which is not one directory up.

        `src/net/client.rs` is the module `net::client`, so its `super` is
        `net`, whose files sit in `src/net/`. The crate root above it is
        reached with `crate::`, never by walking `super` off the importing
        file's own directory.
        """
        root = self._crate(tmp_path)
        client = root / "src" / "net" / "client.rs"

        assert (
            resolve_rust("super::client", client, root)
            == (root / "src" / "net" / "client.rs").resolve()
        )
        assert resolve_rust("super::config", client, root) is None

    def test_self_stays_in_the_importing_directory(self, tmp_path):
        root = self._crate(tmp_path)

        hit = resolve_rust("self::client", root / "src" / "net" / "mod.rs", root)

        assert hit == (root / "src" / "net" / "client.rs").resolve()

    def test_an_item_inside_a_module_lands_on_that_module(self, tmp_path):
        """A grouped import expands to one path per item, so the trailing
        item name has to be dropped to find the file holding it."""
        root = self._crate(tmp_path)

        hit = resolve_rust("crate::config::C", root / "src" / "main.rs", root)

        assert hit == (root / "src" / "config.rs").resolve()

    def test_an_external_crate_stays_unresolved(self, tmp_path):
        root = self._crate(tmp_path)

        assert resolve_rust("serde::Serialize", root / "src" / "main.rs", root) is None
        assert resolve_rust("std::io::Read", root / "src" / "main.rs", root) is None
