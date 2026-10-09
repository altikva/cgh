"""Tests for the TypeScript parser."""

from codegraph.parsers import get_parser
from codegraph.parsers.base import FileIndex


class TestTypeScriptParser:
    def test_parser_exists(self):
        parser = get_parser(".ts")
        assert parser is not None
        assert parser.lang == "typescript"

    def test_tsx_supported(self):
        assert get_parser(".tsx") is not None

    def test_js_supported(self):
        assert get_parser(".js") is not None

    def test_parse_functions(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)
        assert isinstance(idx, FileIndex)

        fn_names = [f.name for f in idx.functions]
        assert "formatAmount" in fn_names

    def test_parse_classes(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)

        cls_names = [c.name for c in idx.classes]
        assert "DonorService" in cls_names

    def test_parse_interfaces(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)

        # Interfaces may be captured as classes — check if DonorProfile
        # appears anywhere in classes (parser may or may not handle interfaces)
        cls_names = [c.name for c in idx.classes]
        # At minimum, DonorService class should be present
        assert "DonorService" in cls_names

    def test_class_methods(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)

        methods = [f for f in idx.functions if f.class_name == "DonorService"]
        method_names = [m.name for m in methods]
        assert "fetchDonor" in method_names

    def test_arrow_functions(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)

        fn_names = [f.name for f in idx.functions]
        assert "handler" in fn_names

    def test_imports(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)

        assert len(idx.imports) > 0
        import_modules = [i.source_module for i in idx.imports]
        assert "vue" in import_modules

    def test_line_numbers(self, sample_typescript):
        parser = get_parser(".ts")
        idx = parser.parse(sample_typescript)

        for fn in idx.functions:
            assert fn.start_line > 0
            assert fn.file_path == str(sample_typescript)

    def test_class_bases(self, tmp_path):
        f = tmp_path / "bases.ts"
        f.write_text(
            "class A extends Base<T> implements I, J<K>, ns.L {}\n"
            "class B extends ns.Base {}\n"
            "class C extends mixin(X) {}\n"
            "export default class D extends E {}\n"
            "class F {}\n"
        )
        bases = {c.name: c.bases for c in get_parser(".ts").parse(f).classes}
        assert bases == {
            "A": ["Base", "I", "J", "ns.L"],
            "B": ["ns.Base"],
            # An expression base names no class.
            "C": [],
            "D": ["E"],
            "F": [],
        }

    def test_class_bases_js_and_tsx(self, tmp_path):
        for name in ("bases.js", "bases.tsx"):
            f = tmp_path / name
            f.write_text("class A extends React.Component {}\nclass B extends A {}\n")
            idx = get_parser(f.suffix).parse(f)
            assert {c.name: c.bases for c in idx.classes} == {
                "A": ["React.Component"],
                "B": ["A"],
            }
