"""Tests for the Python parser."""

from codegraph.parsers import get_parser
from codegraph.parsers.base import FileIndex


class TestPythonParser:
    def test_parser_exists(self):
        parser = get_parser(".py")
        assert parser is not None
        assert parser.lang == "python"

    def test_parse_functions(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)
        assert isinstance(idx, FileIndex)
        assert idx.lang == "python"

        fn_names = [f.name for f in idx.functions]
        assert "validate" in fn_names
        assert "main" in fn_names

    def test_parse_classes(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        cls_names = [c.name for c in idx.classes]
        assert "BaseHandler" in cls_names
        assert "DonationHandler" in cls_names

    def test_class_inheritance(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        donation_handler = next(c for c in idx.classes if c.name == "DonationHandler")
        assert "BaseHandler" in donation_handler.bases

    def test_methods_have_class_name(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        methods = [f for f in idx.functions if f.class_name is not None]
        assert len(methods) > 0
        method_names = [m.name for m in methods]
        assert "handle" in method_names

    def test_imports(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        import_modules = [i.source_module for i in idx.imports]
        assert "os" in import_modules
        assert "pathlib" in import_modules

    def test_docstrings(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        validate_fn = next(f for f in idx.functions if f.name == "validate")
        assert "Validate" in validate_fn.docstring

    def test_line_numbers(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        for fn in idx.functions:
            assert fn.start_line > 0
            assert fn.end_line >= fn.start_line
            assert fn.file_path == str(sample_python)

    def test_call_extraction(self, sample_python):
        parser = get_parser(".py")
        idx = parser.parse(sample_python)

        main_fn = next(f for f in idx.functions if f.name == "main")
        # main() calls DonationHandler() and handler.handle()
        assert len(main_fn.calls) > 0

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        parser = get_parser(".py")
        idx = parser.parse(f)
        assert idx.lang == "python"
        assert len(idx.functions) == 0
        assert len(idx.classes) == 0
